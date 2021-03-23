#!/usr/bin/env python3
# pylint: disable=C0103

"""Example code to demonstrate single-module streaming with the LAN-XI Open API."""

import argparse
import time
import socket
import threading
import selectors
import requests

def receive_thread(sel):
    """On selector event, reads data from associated socket and writes to file."""
    while True:
        events = sel.select()
        for key, _ in events:
            sock = key.fileobj
            data = sock.recv(16384)
            if not data:
                return
            file = key.data
            file.write(data)

def get_measurement_errors(cs):
    """Given a 'cs' array of channelStatus structures, return a collection of error strings."""
    err = []
    for i, sts in enumerate(cs):
        s, prefix = "", f"Channel {i+1}: "
        if sts["anol"] != "none":
            s = s + prefix + f'Analog Overload ({sts["anol"]})'
            prefix = ", "
        if sts["cmol"] != "none":
            s = s + prefix + f'Common Mode Overload ({sts["cmol"]})'
            prefix = ", "
        if sts["cf"] != "none":
            s = s + prefix + f'Cable Fault ({sts["cf"]})'
            prefix = ", "
        if s != "":
            err.append(s)
    return err

parser = argparse.ArgumentParser()
parser.add_argument("addr", help="IP address of the LAN-XI module")
parser.add_argument("-n", "--name", dest="name", default="My Measurement", \
    help="Name of the measurement")
parser.add_argument("-t", "--time", dest="time", default=10, type=int, \
    help="The time (in seconds) of the measurement")
args = parser.parse_args()

# Generate base URL; IPv6 addresses in URL's must be enclosed in square brackets
ip_addr = args.addr.split("%")[0] # Remove IPv6 zone index, if specified
addr_family = socket.getaddrinfo(ip_addr, port=0)[0][0]
base_url = "http://[" + args.addr + "]" if addr_family == socket.AF_INET6 else "http://" + args.addr
base_url = base_url + "/rest/rec"
args.addr = args.addr.replace("%%", "%") # Fix double per cent sign issue on Windows

print(f'Creating {args.time}-second measurement "{args.name}" on module at {ip_addr}')

# Set the module date/time
now = time.time()
requests.put(base_url + "/module/time", data=str(int(now * 1000)))

# Open the recorder and enter the configuration state
requests.put(base_url + "/open", json={"performTransducerDetection": False})
requests.put(base_url + "/create")

# Start TEDS transducer detection
print("Detecting transducers...")
requests.post(base_url + "/channels/input/all/transducers/detect")

# Wait for transducer detection to complete
prev_tag = 0
while True:
    response = requests.get(base_url + "/onchange?last=" + str(prev_tag)).json()
    prev_tag = response["lastUpdateTag"]
    if not response["transducerDetectionActive"]:
        break

# Get the result of the detection
transducers = requests.get(base_url + "/channels/input/all/transducers").json()

# Get the default measurement setup
setup = requests.get(base_url + "/channels/input/default").json()

# Select streaming rather than (the default) recording to SD card
for ch in setup["channels"]:
    ch["destinations"] = ["socket"]

# Configure front-end based on the result of transducer detection
for idx, t in enumerate(transducers):
    if t is not None:
        setup["channels"][idx]["transducer"] = t
        setup["channels"][idx]["ccld"] = t["requiresCcld"]
        setup["channels"][idx]["polvolt"] = t["requires200V"]
        print(f'Channel {idx+1}: {t["type"]["number"] + " s/n " + str(t["serialNumber"])}, '
              f'CCLD {"On" if t["requiresCcld"] == 1 else "Off"}, '
              f'Polarization Voltage {"on" if t["requires200V"] == 1 else "off"}')

# Apply the setup
print(f'Configuring module...')
requests.put(base_url + "/channels/input", json=setup)

# Store streamed data to this file
filename = args.name + ".stream"
stream_file = open(filename, "wb")

# Request the port number that the module is listening on
port = requests.get(base_url + "/destination/socket").json()["tcpPort"]

# Connect streaming socket
stream_sock = socket.create_connection((args.addr, port), timeout=10)
stream_sock.setblocking(False)

# We'll use a Python selector to manage socket I/O
selector = selectors.DefaultSelector()
selector.register(stream_sock, selectors.EVENT_READ, stream_file)

# Start thread to receive data
thread = threading.Thread(target=receive_thread, args=(selector, ))
thread.start()

# Start measuring, this will start the stream of data from the module
requests.post(base_url + "/measurements")
print("Measurement started")

# Print measurement status including any errors that may occur
prev_tag, prev_status, start = 0, "", time.time()
while time.time() - start < args.time:
    response = requests.get(base_url + "/onchange?last=" + str(prev_tag)).json()
    prev_tag = response["lastUpdateTag"]
    status = f'Measuring {response["recordingStatus"]["timeElapsed"]}'
    errors = get_measurement_errors(response["recordingStatus"]["channelStatus"])
    status = status + " " + ("OK" if len(errors) == 0 else  ", ".join(errors))
    if prev_status != status:
        print(status)
        prev_status = status

# Stop measuring
requests.put(base_url + "/measurements/stop")
print("Measurement stopped")

# Close the streaming connection, data file, and recorder
stream_sock.close()
thread.join()
stream_file.close()

requests.put(base_url + "/finish")
requests.put(base_url + "/close")

print(f'Stream saved as "{filename}"')
