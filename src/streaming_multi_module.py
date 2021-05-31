#!/usr/bin/env python3
# pylint: disable=C0103,W0621

"""Example code to demonstrate multi-module streaming with the LAN-XI Open API."""

import argparse
import time
import socket
import sys
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
        if sts is None:
            continue
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

def setup_module(m):
    """Given a module structure 'm', performs TEDS transducer detection
       and configures the module with default measurement settings."""
    # Start TEDS transducer detection
    requests.post(m["base_url"] + "/channels/input/all/transducers/detect")
    # Wait for transducer detection to complete
    prev_tag = 0
    while True:
        response = requests.get(m["base_url"] + "/onchange?last=" + str(prev_tag)).json()
        prev_tag = response["lastUpdateTag"]
        if not response["transducerDetectionActive"]:
            break
    # Get the result of the detection
    transducers = requests.get(m["base_url"] + "/channels/input/all/transducers").json()
    # Get the default measurement setup
    m["setup"] = requests.get(m["base_url"] + "/channels/input/default").json()
    # Configure front-end based on the result of transducer detection
    for idx, t in enumerate(transducers):
        if t is not None:
            # A transducer was found on this channel
            m["setup"]["channels"][idx]["transducer"] = t
            m["setup"]["channels"][idx]["ccld"] = t["requiresCcld"]
            m["setup"]["channels"][idx]["polvolt"] = t["requires200V"]
            print(f'Module {m["number"]} Channel {idx+1}: '
                  f'{t["type"]["number"] + " s/n " + str(t["serialNumber"])}, '
                  f'CCLD {"On" if t["requiresCcld"] == 1 else "Off"}, '
                  f'Polarization Voltage {"on" if t["requires200V"] == 1 else "off"}')
    # Submit measurement setup to module
    requests.put(m["base_url"] + "/channels/input", json=m["setup"])

def await_state(description, modules, name, state, timeout=30):
    """Given a list of 'modules', waits for each module to enter the specified state."""
    prev_remaining = len(modules)
    print(f'Waiting for {prev_remaining} module(s) to {description}...')
    start = time.time()
    while time.time() - start < timeout:
        for m in modules:
            if name not in m or m[name] != state:
                # Get the current state from the module
                m[name] = requests.get(m["base_url"] + "/onchange").json()[name]
        # How many modules are we still waiting for?
        remaining = sum(1 for x in modules if x[name] != state)
        if remaining == 0:
            # All modules have entered the expected state.
            print("Done")
            return
        if remaining != prev_remaining:
            print(f'Waiting for {remaining} module(s) to {description}...')
            prev_remaining = remaining
        time.sleep(1)
    # Not all modules entered the expected state before the timeout.
    sys.exit(f'Some modules did not {description}')

parser = argparse.ArgumentParser()
parser.add_argument("addrs", nargs='+', \
    help="IP addresses of two or more LAN-XI modules, PTP master listed first")
parser.add_argument("-n", "--name", dest="name", default="My Measurement", \
    help="Name of the measurement")
parser.add_argument("-t", "--time", dest="time", default=10, type=int, \
    help="The time (in seconds) of the measurement")
parser.add_argument("-d", "--ptp-domain", dest="ptp_domain", default=42, type=int, \
    help="The PTP domain to use for synchronization")
args = parser.parse_args()

if len(args.addrs) < 2:
    parser.error("at least two IP addresses must be provided")

# Process IP addresses and generate base URLs for all modules
modules = []
for idx, addr in enumerate(args.addrs):
    # Remove IPv6 zone index, if specified
    ip_addr = addr.split("%")[0]
    addr_family = socket.getaddrinfo(ip_addr, port=0)[0][0]
    # IPv6 addresses in URL's must be enclosed in square brackets
    base_url = "http://[" + addr + "]" if addr_family == socket.AF_INET6 else "http://" + addr
    base_url = base_url + "/rest/rec"
    modules.append({"addr": addr.replace("%%", "%"), "base_url": base_url, "number": idx + 1})

addr_list = ", ".join(map(lambda m: m["addr"], modules))
print(f'Creating {args.time}-second measurement "{args.name}" on modules at {addr_list}')

print("Configuring modules to use PTP synchronization")
master = modules[0]
now = time.time() * 1000
sync_params = {"synchronization": \
    {"mode": "ptp", "settime": int(now), "difftime": 0, "domain": args.ptp_domain}}
for m in modules:
    sync_params["synchronization"]["preferredMaster"] = m == master
    requests.put(m["base_url"] + "/syncmode", json=sync_params)

await_state("lock onto PTP", modules, "ptpStatus", "Locked", timeout=600)

# Reverse the list of modules so the master is last.
# This will make it easier to make the remaining requests in the right order.
modules.reverse()

open_params = {"singleModule": False, "performTransducerDetection": False}
for m in modules:
    requests.put(m["base_url"] + "/open", json=open_params)

await_state("start sampling", modules, "inputStatus", "Sampling")

for m in modules:
    requests.put(m["base_url"] + "/create")

print("Detecting transducers and setting up measurement...")
for m in modules:
    setup_module(m)

await_state("settle", modules, "inputStatus", "Settled")

# Synchronize sample clocks across all modules
for m in modules:
    requests.put(m["base_url"] + "/synchronize")

await_state("synchronize", modules, "inputStatus", "Synchronized")

for m in modules:
    requests.put(m["base_url"] + "/startstreaming")

await_state("start streaming", modules, "inputStatus", "Streaming")

# We'll use a Python selector to manage socket I/O
selector = selectors.DefaultSelector()

# For each module, create a file to store streaming data.
# Then connect to each module, and associate each module's data file with the connection.
for m in modules:
    # Store streamed data to this file
    m["stream_filename"] = args.name + "." + str(m["number"]) + ".stream"
    m["stream_file"] = open(m["stream_filename"], "wb")

    # Request the port number that the module is listening on
    m["port"] = requests.get(m["base_url"] + "/destination/socket").json()["tcpPort"]

    # Connect streaming socket
    m["stream_sock"] = socket.create_connection((m["addr"], m["port"]), timeout=10)
    m["stream_sock"].setblocking(False)

    # Register socket and file with selector
    selector.register(m["stream_sock"], selectors.EVENT_READ, m["stream_file"])

# Start thread to receive data from all modules
thread = threading.Thread(target=receive_thread, args=(selector, ))
thread.start()

# Start measurement
for m in modules:
    requests.post(m["base_url"] + "/measurements")

print("Measurement started")

# Print measurement status including any errors that may occur
prev_tag, prev_status, start = 0, "", time.time()
while time.time() - start < args.time:
    master = modules[-1]
    response = requests.get(master["base_url"] + "/onchange?last=" + str(prev_tag)).json()
    prev_tag = response["lastUpdateTag"]
    status = f'Measuring {response["recordingStatus"]["timeElapsed"]}'
    if prev_status != status:
        print(status)
        prev_status = status
        for m in modules:
            response = requests.get(m["base_url"] + "/onchange").json()
            errors = get_measurement_errors(response["recordingStatus"]["channelStatus"])
            if len(errors) > 0:
                print(f'  Module {m["number"]} {", ".join(errors)}')

# Stop measurement
for m in modules:
    requests.put(m["base_url"] + "/measurements/stop")

print("Measurement stopped")

# Close the streaming connections, data files, and recorder instances on each module
for m in modules:
    m["stream_sock"].close()
    m["stream_file"].close()
    requests.put(m["base_url"] + "/finish")
    requests.put(m["base_url"] + "/close")
    print(f'Stream from {m["addr"]} saved as "{m["stream_filename"]}"')

thread.join()

# Optional: switch modules back to stand-alone synchronization
for m in modules:
    sync_params = {"synchronization": {"mode": "stand-alone"}}
    requests.put(m["base_url"] + "/syncmode", json=sync_params)
