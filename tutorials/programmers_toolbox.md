# Open API Programmer's Toolbox

The Toolbox page is a collection of cookbook-style code samples and snippets of information useful for working with the Open API.

## Installing Python

Python 3.7 or later is required to run most of the example code in this repository.

Most Linux distributions support this out of the box.

Windows users may need to follow the guidelines from Microsoft on [how to install Python on Windows](https://docs.microsoft.com/en-us/windows/python/beginners).

## Multi-Client Support

Open API supports multiple controlling client applications at any time, including Notar and the module button/display.

For instance, the Notar recorder application could be used to monitor measurements carried out by a separate software application.

For an example of this, open the homepage of the LAN-XI module and click *Open recorder application*. Then run one of the example applications, such as the [streaming sample](streaming_single_module.md), and watch how Notar shows the elapsed time and signal levels while the measurement is ongoing:

![Running Notar side-by-side with an Open API application](../images/multi_client_support.gif)

Another example would be to have a software application prepare the module for a measurement, and then prompt the user to press the Control button on the module to start measuring. To prevent accidentally starting and stopping a measurement, it is also possible to disable the Control button.

## Transducer Detection and Setup

Open API automatically performs TEDS transducer detection on start-up.

This may be undesirable for a number of reasons:

* it can be difficult to determine when the detection process starts and finishes
* it prolongs the time required to set up new a measurement from scratch
* the transducer configuration may already be known, and/or none of the connected transducers support TEDS

In those cases, it is possible to instruct Open API to skip automatic detection when the recorder is opened:

```python
base_url = "http://<ip>"

requests.put(base_url + "/rest/rec/open", json={"performTransducerDetection": False})
```

Transducer detection can be performed on-demand as follows:

```python
base_url = "http://<ip>"

# Start TEDS transducer detection
requests.post(base_url + "/rest/rec/channels/input/all/transducers/detect")

# Wait for transducer detection to complete
prev_tag = 0
while True:
    response = requests.get(base_url + "/rest/rec/onchange?last=" + str(prev_tag)).json()
    prev_tag = response["lastUpdateTag"]
    if not response["transducerDetectionActive"]:
        break

# Get the result of the detection
transducers = requests.get(base_url + "/rest/rec/channels/input/all/transducers").json()
```

The returned result of the detection includes the fields `requiresCcld` and `requires200V`, which can be used to configure the front-end e.g. to enable the [CCLD](https://www.bksv.com/en/transducers/signal-conditioning/ccld) supply.

However, be aware that those values while mostly accurate are nothing more than best guesses. They are based on a transducer database embedded in the module firmware, combined with heuristics to determine the requirements of the transducer in question; in other words, they are merely suggestions and should be overridden by the end-user if needed.

Also note that the use of polarisation voltage requires a front-panel with LEMO connectors such as the [UA-2101-060](https://www.bksv.com/en/instruments/daq-data-acquisition/lan-xi-daq-system/frontpanels/ua-2101).

Not all transducers support TEDS. It is possible to manually add transducer information including the transducer type, serial number, sensitivity and engineering unit to the measurement setup.

## Setting the Time

Setting the time is good practice when performing single-module measurements.

LAN-XI modules don't have a battery-backed real-time clock, but rely on available NTP servers to obtain the current date and time.

This means that the module will use an incorrect time if an NTP server is not reachable, such as when the module is connected directly (point-to-point) to a PC, or is on a local network with no Internet access.

For PTP-synchronized, multi-module measurements the modules will obtain the time from the PTP master.

## Front-panel LED's

The Status LED immediately below the module display turns red if an error occurs during a measurement, such as data loss due to a bad network connection, or the SD card filling up. Overload and other signal-related errors are reported through the circular LED's-

The circular LED on each channel is used to indicate a variety of information:

Colour | Meaning
--- | ---
Off | Channel not in use
Green | Data from the channel is being streamed over the network, or recorded to SD card
Yellow | Channel is enabled for measurement but not currently measuring, or TEDS detection is in progress
Red | Channel is measuring but there is an overload or other signal conditioning error
Magenta | The channel is measuring and, although the channel is not currently overloaded, there was an overload or other error during the current measurement
Blue | The channel is an output or generator channel

## IPv6

Open API supports IPv6 for the HTTP-based protocol, but not for streaming at the time of writing.

IPv6 will be supported in a future firmware release, and the code samples in this repository will work with IPv6 without any modifications.

The module's IPv6 addresses are shown on the module's homepage; click *Network* and look under the *IPv6* heading.

All LAN-XI modules have a link-local IPv6 address, identified by the prefix `fe80::/10`.

In order to use the link-local address to connect to the module, the name of the connecting client's Ethernet interface must be appended to the link-local address.

For instance, running the streaming sample on a Linux PC, and using the `wlp82s0` network interface on the PC to connect to the LAN-XI module, would look like this:

```shell
# Linux
$ ./streaming_single_module.py fe80::280:daff:fe00:8db0%wlp82s0
```

Note that on Microsoft Windows, `%` is a special character and requires escaping by an additional `%`. Example:

```shell
# Windows
>python.exe ./streaming_single_module.py fe80::280:daff:fe00:8db0%%33
```

## mDNS/Bonjour

LAN-XI modules respond to mDNS requests for `_http._tcp` services.

## Other Network Protocols

LAN-XI currently does not support HTTPS, WebSocket or IPsec.

## Module Information

It is often useful to query the capabilities of a module in terms of bandwidth, available channels, filters, etc.

Software can use this to present end-users with a list of options, or automatically select settings to suit the measurement task.

To request information about the module, execute

```python
base_url = "http://<ip>"

requests.put(base_url + "/rest/rec/module/info")
```

The response includes:

* the number of input- and output channels
* available sample rates (divide by 2.56 to obtain the bandwidth)
* supported input ranges and filter settings
* whether an SD card is inserted
* the module type and serial number
* information on the mounted front panel
* firmware and hardware version numbers

## Module States

The module *state* determines the type of API requests that can be made.

Most of the time the programmer won't need to think about this, but you may run into the odd error (typically HTTP status 403) while experimenting with the API.

In many cases the reason for the error is obvious, such as attempting to perform transducer detection while a measurement is in progress.

For documentation on this please refer to our [reference documentation](https://github.com/hbk-world/LAN-XI-FW/blob/master/Open%20APIv17draft.pdf), which lists each API request along with the permissible module states.

## Measurement Limitations

Currently, the `bandwidth` and `destinations` values on each channel must be the same.

For example, it is not possible to configure channel 1 to measure at 51.2 kHz bandwidth, and channel 2 at 25.6 kHz bandwidth.

Similarly, all channels must be either streamed (`destinations = [ "socket" ]`) or stored on SD card (`destinations = [ "sd" ]`), the protocol does not support streaming some channels and storing others on SD card.

Finally, only one destination can be selected at a time. The modules are unable to both stream and store acquired data at the same time.

## Error Handling

The code examples omit error handling in the interest of clarity.

In a production environment, the HTTP status code should be checked after all requests.

A status code in the 4xx range indicates a client error, while a status code of 5xx means there was an error on the server. The module will include an error message in the response body.

A 5xx status may have left the module in an unstable state. The recommended action after a 5xx error is to reboot the module by executing

```python
base_url = "http://<ip>"

requests.put(base_url + "/rest/rec/reboot")
```

HBK appreciates reports of 5xx errors, if possible with a sample program or description of events leading up to the error.

Instances of 5xx errors are stored in the module's eventlog, available on the *Status* homepage. The error code and additional information stored in the log may also be helpful when diagnosing errors.

## Licenses

Older firmware versions may require a license to use SD recording or streaming.

You may experience errors that indicate that a license is missing, typically in the form of HTTP status 403 being returned on certain API calls.

Should this happen, we recommend updating the module firmware to version 2.10.0.344 or later, where all API restrictions have been removed.
