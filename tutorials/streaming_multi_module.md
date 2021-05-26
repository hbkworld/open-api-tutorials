# Multi-Module Streaming with Open API

This Open API tutorial demonstrates how to set up two or more LAN-XI modules to capture sample-synchronous data.

The example code configures PTP synchronization and then connects to two or more LAN-XI modules, runs TEDS transducer detection, captures about ten seconds of data from all input channels on all the modules, and saves the data to files named `My Measurement.<number>.stream`.

The follow-up article [Interpreting Data from an Open API Stream](streaming_interpretation.md) explains how to interpret the data from the `My Measurement.<number>.stream` files.

You may also be interested in

* [Streaming with Open API](streaming_single_module.md)
* [Open API Programmer's Toolbox](programmers_toolbox.md)

# Example Code

The example code is written in Python.

Python 3.7 or later is required, refer to the Toolbox page for [instructions on how to install Python](programmers_toolbox.md).

```python
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

# Close the streaming connections, data files, and recorder instances on eacn module
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
```
## Running the Example

To obtain the example code, download or clone this repository from GitHub.

The code uses the Python `requests` package, so run the `pip3` package manager to add this package as shown:

```shell
pip3 install requests
```

To run the code you will need at least two LAN-XI modules and, optionally, one or more transducers with [TEDS](https://en.wikipedia.org/wiki/IEEE_1451#Transducer_electronic_data_sheet) support.

Note that LAN-XI module types 3676 and 3677 do not support the Precision Time Protocol required for synchronization, and thus will not work with this tutorial.

Determine the IP address of each LAN-XI module, this is shown on the display at the front of the module.

Then run the example, passing the IP address of each module on the command line:

```shell
# Windows
>python.exe .\streaming_multi_module.py 192.168.1.101 192.168.1.102

# Linux
$ ./streaming_multi_module.py 192.168.1.101 192.168.1.102
```
Note: To see the full range of available options, run the script with the `-h` parameter.

This screen capture shows the output from running the example with three LAN-XI modules:

![Typical output from running the code sample](../images/streaming_multi_module.gif)

## The Precision Time Protocol

LAN-XI uses the [Precision Time Protocol (PTP)](https://en.wikipedia.org/wiki/Precision_Time_Protocol) to synchronize modules across a network. Typically, the performance of the synchronization protocol is better than ±200 ns, or ±2° at 25.6 kHz. For details on this refer to the [LAN-XI Product Data](https://www.bksv.com/-/media/literature/Product-Data/bp2215.ashx).

The example code accepts a list of IP addresses, one for each LAN-XI module to include in the measurement. The module whose IP address is listed first will become the *PTP master*; all other modules will follow the clock reference provided by the master.

In cases where multiple PTP systems are active on the same network care must be taken to ensure they do not interfere with each other. The [PTP Domain Number](https://en.wikipedia.org/wiki/Precision_Time_Protocol#Domains) provides a way to organise modules into groups that synchronize with a particular master. It's a number in the range of 4 to 127 inclusive, usually administered within your organisation.

If you know there is no other PTP activity on the network then you won't have to do anything; the example code will simply use a default, hard-coded PTP Domain Number. Otherwise, you may need to request a PTP Domain Number from your network administrator and pass this to the example code by means of the `-d` command line option.

## Code Walk-Through

The example code was based on the (single-module) [Streaming with Open API](streaming_single_module.md) tutorial, with the addition of PTP configuration and housekeeping associated with managing multiple modules.

We start off with parsing the command line arguments and building a `modules` list containing base URL's and processed IP addresses. We also assign a `number` to each module, this is simply an integer used to identify each module in the program output and generated filenames.

Then we configure the modules to use PTP.

This involves sending a collection of synchronization parameters to each module, including the PTP Domain to use and whether the module in question should act as a PTP master. In the example code, the first LAN-XI module specified on the command line will take on the role as PTP master, but it is also possible to use a separate PTP device as the master.

Multi-module setup is inherently more complicated than single-module configurations, and certain requests need to happen in the right order.

For instance, the synchronization parameters must be sent to the PTP master first, followed by all the other modules. Also, synchronization must be configured before the Open API instance on each module is opened, i.e. before `PUT /open`.

After we've sent the synchronization parameters, we also need to wait for all modules to lock on to the master clock. The time that this takes varies a bit but is generally on the order of 1-2 minutes. The PTP status can be queried from each module, so in this case we use the convenience function `await_state` to wait for all the modules to enter a `ptpStatus` of `Locked`.

The remaining requests sent by the example code must be issued in the reverse order, i.e. the PTP master must be the last module to receive each request, so we reverse the list of modules to make it easier to meet that requirement.

We can now proceed with opening the recorder, performing transducer detection, and applying the measurement setup to each module. The details of how to do this are covered in the [Streaming with Open API](streaming_single_module.md) tutorial and won't be repeated here.

Importantly, in a multi-module scenario the client must be careful to ensure that each module is in the expected state before issuing new requests. Again, the `await_state` function is used to confirm this. The client must also manage synchronization of the sample clock, which is done after sending the measurement setup.

The remaining parts of the example are similar to the [Streaming with Open API](streaming_single_module.md) example, including creating files to store the streaming data from each module, connecting sockets to each module, starting and stopping the measurement, and printing status information while measuring.

When the measurement has finished, we switch the modules back to using stand-alone synchronization. If you intend to make a series of measurements you may wish to omit this from your code so as to avoid a lengthy wait for the PTP lock before each measurement.

Proceed with [Interpreting Data from an Open API Stream](streaming_interpretation.md) to learn how to convert the stored data to a format useful to your application.

# Frame Sync

All LAN-XI modules(*) can be configured to use PTP synchronization, irrespective of whether they are stand-alone modules or are mounted in a LAN-XI frame.

However, modules mounted in a frame will always override any synchronization parameters and instead use a synchronization mechanism internal to the frame, commonly referred to as *frame sync*. This happens transparently and client software does not need to treat frame-mounted modules any different than stand-alone modules.

Frame sync is based on a dedicated sync line and is more accurate than PTP. The module acting as the *frame controller* (the module situated in the left-most slot of the frame) manages the sync line for all other modules in the frame. Simultaneously, frame controllers can act as PTP masters for other frames and stand-alone modules on the network, or they can lock on to another PTP master on the network.

LAN-XI frames also provide the ability to use GPS as a synchronization source.

For a full overview of synchronization options refer to the [LAN-XI Product Data](https://www.bksv.com/-/media/literature/Product-Data/bp2215.ashx) documentation.

(*) With the exception of types 3676 and 3677, as noted earlier

# Multi-Socket Streaming

In the example code, data from all input channels on each module is streamed on a single TCP connection.

Open API offers the ability to stream each channel on a separate connection, referred to as multi-socket streaming.

This may be useful e.g. to stream 'monitor' channels directly to a software application for display, whilst streaming the remaining channels to a different network host for processing or storage.

To configure modules to use multi-socket streaming, change the example code as follows:

* Set the `destination` on all enabled channels to `multi-socket`
* Make a `GET` request to `/rest/rec/destination/sockets`to obtain a list of TCP port numbers to connect to. The response will be a JSON array of port numbers. The first port number in the array will contain data from the first enabled channel, and so on
* Set up connections to each TCP port before starting the measurement
