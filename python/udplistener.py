""" Collector that returns events and traces generated from a URL.

This is, philosophically, to a stripped-down, localhost-only, synchronous
tracelyzer/transformer/etlworker. We write our own tracelyzer/transformer, and
import parts of the etlworker.

This is the main backend for testing oboe stacks that have been set up
elsewhere. This module provides helper functions (called get_*) that can be
called as part of a unit/functional testing framework.

Note that because we are not testing the stacks' functionality here, no errors
are reported. That means 500s, 403s, 404s, etc. will all just complete and
return with no indication. This is desirable because we sometimes want to test
those conditions, and the trace reported should still be valid. Make sure your
test stack is valid before writing tests against it!

All public methods currently make the following assumptions:
 - The stack is set up and accessible via HTTP from this machine.
 - All events are generated on this machine via UDP.
"""
import logging
import os
import platform
import socket
import sys
import types
from six.moves import urllib
from pprint import pprint, pformat
import functools
import json
import time
import bson
from mdict import mdict

LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = int(os.environ.get('STACK_COLLECTOR_PORT', 7831))
REQUEST_TIMEOUT = 100 # seconds

# the buffer size can be set to other values
# this value was originally chosen as the largest possible buffer size.
# Note that you do not have to choose the biggest buffer size.
# A larger buffer size reduces cpu activity but it also
# increases chances of socket timeout.
BUFFER_SIZE = int(os.environ.get('UDPLISTEN_BUFFER_SIZE', 131071))

# Guard against infinite events
MAX_EVENTS = int(os.environ.get('UDPLISTEN_MAX_EVENTS', 200))

# put in import hooks to skip loading python modules that are a
# pain (or impossible, for windows) to compile natives bindings for,
# and are not actually needed by the way tracelons code is used here.
class DummyPycassa:
    def find_module(self, name, path=None):
        if name == 'pycassa':
            m = types.ModuleType('dummy')
            m.NotFoundException = 'dummy'
            sys.modules['pycassa.cassandra'] = m
            sys.modules['pycassa.cassandra.ttypes'] = m
            return self
    def load_module(self, name): return self
sys.meta_path.append(DummyPycassa())

class DummyPyLibmc:
    def find_module(self, name, path=None):
        if name == 'pylibmc': return self
    def load_module(self, name):
        return self
sys.meta_path.append(DummyPyLibmc())

if platform.system() == 'Windows':
    class DummySnappy:
        def find_module(self, name, path=None):
            if name == 'snappy': return self
        def load_module(self, name):
            return self
    sys.meta_path.append(DummySnappy())

class UdpCollector(object):
    def __init__(self, host, port, timeout):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(timeout)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, port,))
    def __enter__(self):
        return self.socket
    def __exit__(self, type, value, traceback):
        self.socket.close()

def get_trace(*args, **kwargs):
    """ Make a call to the given URL, and return the extent_repr.

    With kwarg events provided, uses those events (returned by a prior call to
    get_events) instead of making a second request.

    For this to work, the tracelons repo must be on the pythonpath. Something
    like the following should cover the current use case and all future
    developments.

    If the URL calls a redirect, events with a different X-Trace ID than the
    first event will be ignored.

    Same arguments as get_events.
    """
    # Defer this import until here -- it may not work.
    try:
        if platform.system() == 'Windows':
            # add tracelons to the module search path, if PYTHONPATH is not set
            # this assumes git repos are cloned under 'C:\sources'
            if 'PYTHONPATH' not in os.environ:
                sys.path.insert(0, 'C:\\sources\\tracelons')
        # make oboe module quiet so we don't see messages like no-op mode, etc.
        logging.getLogger('oboe').setLevel(logging.CRITICAL)
        from common.trace import DebugTrace
    except ImportError as e:
        print('Bummer! {0}'.format(e))
        print('You might want to set your PYTHONPATH, eg:')
        print('$ export PYTHONPATH="/deploy/tracelons/:/deploy/tracelons/transformer:/deploy/tracelons/tracelytics"')
        raise

    if 'events' in kwargs:
        events_raw = kwargs['events']
    else:
        events_raw = get_events(*args, **kwargs)

    events = [data.to_dict() for data in events_raw]

    for ev in events:
        if 'op_id' not in ev and 'X-Trace' in ev:
            ev['op_id'] = ev['X-Trace'][42:58]
    xtrace = events[0].get('X-Trace')[2:42] if events else None
    first_trace_events = [ev for ev in events if ev.get('X-Trace')[2:42] == xtrace]
    trace = DebugTrace('default', xtrace, events=first_trace_events)

    return trace.extent_repr()

def get_events(url, num_expected=1000, timeout=3, strict=True, inits_expected=True,
               jmx_expected=False, req_method="GET", req_data=None,
               req_type='html'):
    """ Wrapper for get_events_and_response() that just returns events.
    if req_method="GET", req_data will be ignored
    if req_method="POST", req_data could be a dictionary of values or an encoded string
        of values to be sent with the post request
    if req_method="PUT", req_data is a string that will be sent to the body
    """
    events = get_events_and_response(url, num_expected, timeout, strict, inits_expected,
                                     jmx_expected, req_method, req_data,
                                     req_type)[0]
    return events

def get_events_and_response(url, num_expected=1000, timeout=5, strict=True, inits_expected=True,
                            jmx_expected=False, req_method="GET",
                            req_data=None, req_type='html', headers=None):
    """ Returns the events, init events, and the response object
        init events are in a list
        response object will have a 'code' property for when it is 200
        and also for "exotic http errors"
            read https://docs.python.org/2/library/urllib2.html for more info
        however, if the domain is not available at all (500 code),
        then the response will be the Exception object which will not have a code property
    Parameters:
    url -- The URL to call. Can be a str, urllib2.Request object, or callable() object.
    num_expected [1000] -- The number of events to expect. If more are found, an error is thrown.
    timeout [3] -- The socket listen timeout in seconds
    strict [True] -- only valid with num_expected; ensure that num == num_expected.
    inits_expected [True] -- If True, __Init event pairs will be filtered into the
    returned `inits` array.  The number of remaining events are checked against num_expected.
    Set to False to skip this processing.
    """
    func = url if callable(url) else functools.partial(_make_call, url, req_method, req_data, req_type, headers)

    with UdpCollector(LISTEN_HOST, LISTEN_PORT, timeout) as uc:
        start_time = time.time()
        full_data = []
        try:
            response = func()
        except Exception as ex:
            response = ex

        while len(full_data) < MAX_EVENTS:
            try:
                ret = uc.recv(BUFFER_SIZE)
                full_data.append(ret)
            except socket.timeout:
                break
            else:
                if time.time() - start_time > timeout:
                    break

    events = []
    for data in full_data:
        if data:
            bson_data = bson.BSON(data)
            try:
                # to_dict() is used for very old pymongo library
                # it has been replaced by decode() now.
                evts = bson_data.to_dict(mdict)
            except AttributeError:
                evts = bson_data.decode(codec_options=bson.CodecOptions(document_class=mdict))
            events.append(evts)

    inits = []
    jmxs = []
    measurements = []

    # Only prints on failure when run through nose
    print('Events collected: {0}'.format(len(events)))
    _debug_print(events)

    _filter_measurements(events, measurements)
    print('Measurements after filtering:')
    _debug_print(measurements)

    if inits_expected:
        _filter_inits(events, inits)
        print('Inits after filtering:')
        _debug_print(inits)
    if jmx_expected:
        _filter_jmxs(events, jmxs)
        print('JMX events after filtering:')
        _debug_print(jmxs)
    print('Events after filtering:')
    _debug_print(events)
    if num_expected != -1:
        if len(events) > num_expected:
            raise ExtraEventsError(num_expected, len(events))
        if strict and len(events) != num_expected:
            raise MissingEventsError(num_expected, len(events))
    return events, inits, response

def get_metrics(urls, req_method="GET", timeout=45, req_type='html', req_data=None):
    """ Sends http requests based on the provided urls and collect the corresponding metrics messages

    Keyword arguments:
    urls -- The URLS to call.
    req_method -- The Http method for the call
    timeout -- timeout used for waiting for metrics response, should be set slightly greater than the metrics reporting interval
    """
    print('Collecting metrics from udp socket {0}:{1}'.format(LISTEN_HOST, LISTEN_PORT))
    udpsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udpsock.settimeout(1)
    udpsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udpsock.bind((LISTEN_HOST, LISTEN_PORT,))

    start_time = time.time()
    try:
        for url in urls:
            _make_call(url, req_method, req_data, req_type)
    except Exception as ex:
        print ('error on {0}'.format(urls))
        return None
    
    metrics = []
    keep_receiving = True
    first_ping = False
    timer_5s = 0
    
    while keep_receiving:
        if (time.time() - start_time > timeout):
            print('Failed to collect metrics after waiting for {0}'.format(timeout))
            keep_receiving = False
        else:
            if timer_5s > 0 and (time.time() - timer_5s > 5):
                keep_receiving = False
        try:
            ret = udpsock.recv(BUFFER_SIZE)
            if ret:
                bson_data = bson.BSON(ret)
                try:
                    data = bson_data.to_dict(mdict)
                except AttributeError:
                    data = bson_data.decode(codec_options=bson.CodecOptions(document_class=mdict))

                if "measurements" in data: #then it's a metrics message
                    for m in data["measurements"]:
                        if "tags" in m or first_ping or "IsCustom" in m:
                            metrics.append(data)
                            data = None
                            if first_ping:
                                keep_receiving = False
                            elif timer_5s == 0:
                                timer_5s = time.time()
                            break
            else:
                print('Failed to collect metrics from udp socket {0}:{1} gave empty response'.format(LISTEN_HOST, LISTEN_PORT))
        except socket.timeout:
            pass

    udpsock.close()
    return metrics


class TimeoutException(Exception):
    pass

class ExtraEventsError(Exception):
    """ Raised when more events than expected were collected. """
    def __init__(self, expected, num):
        super(ExtraEventsError, self).__init__('Expected %s events, found at least %s events.' % (expected, num))

class MissingEventsError(Exception):
    """ Raised when more events than expected were collected. """
    def __init__(self, expected, num):
        super(MissingEventsError, self).__init__('Expected %s events, found %s events.' % (expected, num))

def _make_call(url, req_method="GET", req_data=None, req_type='html', headers=None):
    """ Makes a call to a URL, and returns the result as a string.
    Parameters:
    url: the url to which you'd send the request
    req_method: the http method {GET, POST, PUT, HEAD, DELETE,...
    req_data: the request data to send (only when req_method=POST or PUT)
    req_type: default='html' , other possible values: xml, json
    if req_method="GET", req_data will be ignored
    if req_method="POST", req_data could be a dictionary of values or an encoded string
        of values to be sent with the post request
    """
    try:
        if type(url) != str:
            return urllib.request.urlopen(url)
        opener = urllib.request.build_opener(urllib.request.HTTPHandler)
        if req_method in ['PUT', 'POST']:
            if req_data is not None:
                if req_type == 'json':
                    req_data = json.dumps(req_data)
                elif req_type == 'html' and type(req_data) == dict:
                    req_data = urllib.parse.urlencode(req_data) # converts to encoded string
                elif req_type == 'xml':
                    req_data = json.dumps(req_data)
                else:
                    raise Exception("The request type %s is not supported yet." % req_type)

            request = urllib.request.Request(url, data=req_data)
            if req_type in ['json', 'xml']:
                request.add_header('Content-Type', 'application/%s' % req_type)

        else:
            request = urllib.request.Request(url)

        if headers is not None:
            for key, value in headers.items():
                request.add_header(key, value)

        request.get_method = lambda: req_method
        resp = opener.open(request, timeout=REQUEST_TIMEOUT)
        return resp
    except urllib.error.HTTPError as err:
        return err

def _debug_print(eventlist):
    if not eventlist:
        print('[ Empty list ]')
    else:
        for i, ev in enumerate(eventlist):
            print('{0} {1}'.format(i, pformat(ev)))

def _filter_inits(events, inits):
    """ filters "init" events to the given array """
    init_events = [e for e in events if '__Init' in e and str(e['__Init']) == 'True']
    init_events += [e for e in events if 'ConnectionInit' in e and str(e['ConnectionInit']) == 'True']
    # populate the inits array
    inits += init_events
    if inits:
        # strip init events from the event array
        events[:] = [e for e in events if '__Init' not in e or str(e['__Init']) != 'True']
        events[:] = [e for e in events if 'ConnectionInit' not in e or str(e['ConnectionInit']) != 'True']
    print('Done filtering, found {0} Init events'.format(len(inits)))

def _filter_jmxs(events, jmxs):
    """ filters "jmx" events to the given array """
    jmx_events = [e for e in events if 'Layer' in e and e['Layer'] == 'JMX']
    # populate the jmxs array
    jmxs += jmx_events
    if jmxs:
        # strip jmx events from the event array
        events[:] = [e for e in events if e['X-Trace'] not in [j['X-Trace'] for j in jmxs]]
    print('Done filtering, found {0} JMX events'.format(len(jmxs)))

def _filter_measurements(events, measurements):
    """ filters "measurements" events, nee heartbeats and counters,
    to the given array
    """
    measurements_events = [e for e in events if 'measurements' in e
                           and isinstance(e['measurements'], list)]
    # populate the measurements array
    measurements += measurements_events
    if measurements:
        # strip measurements events from the event array
        events[:] = [e for e in events if 'measurements' not in e
                     or not isinstance(e['measurements'], list)]
    print('Done filtering, found {0} Measurements events'.format(len(measurements)))

def get_events_via_udp(udp, url, num_expected=1000, timeout=3, strict=True, inits_expected=True,
               jmx_expected=False, req_method="GET", req_data=None,
               req_type='html', repeat_times = 1):
    """ Wrapper for get_events_and_response() that just returns events.
    if req_method="GET", req_data will be ignored
    if req_method="POST", req_data could be a dictionary of values or an encoded string
        of values to be sent with the post request
    if req_method="PUT", req_data is a string that will be sent to the body
    :params url:string  request url
    """
    use_udp = not udp is None
    if udp is None:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.settimeout(timeout)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((host, port,))

    func = functools.partial(_make_call, url, req_method, req_data, req_type, headers)

    start_time = time.time()
    full_data = []
    try:
        for _ in range(repeat_times):
            response = func()
    except Exception as ex:
        response = ex

    while len(full_data) < MAX_EVENTS:
        try:
            ret = udp.recv(BUFFER_SIZE)
            full_data.append(ret)
        except socket.timeout:
            break
        else:
            if time.time() - start_time > timeout:
                break
    events = []
    for data in full_data:
        if data:
            bson_data = bson.BSON(data)
            try:
                # to_dict() is used for very old pymongo library
                # it has been replaced by decode() now.
                evts = bson_data.to_dict(mdict)
            except AttributeError:
                evts = bson_data.decode(codec_options=bson.CodecOptions(document_class=mdict))
            events.append(evts)

    inits = []
    jmxs = []
    measurements = []

    # Only prints on failure when run through nose
    print('Events collected: {0}'.format(len(events)))
    _debug_print(events)

    _filter_measurements(events, measurements)
    print('Measurements after filtering:')
    _debug_print(measurements)

    if inits_expected:
        _filter_inits(events, inits)
        print('Inits after filtering:')
        _debug_print(inits)
    if jmx_expected:
        _filter_jmxs(events, jmxs)
        print('JMX events after filtering:')
        _debug_print(jmxs)
    print('Events after filtering:')
    _debug_print(events)
    if num_expected != -1:
        if len(events) > num_expected:
            raise ExtraEventsError(num_expected, len(events))
        if strict and len(events) != num_expected:
            raise MissingEventsError(num_expected, len(events))
    return events, inits, response

if __name__ == "__main__":
    #import sr
    #events, inits, text = get_events_and_response(sr.reload_apache,0)
    pprint(get_events('http://localhost'))
