""" Base module for testing instrumentation by hitting a URL.

Some advice: if you can run an assertion against a bag of events (from
get_events), do it. Only run tests that require the full graph against the
graph (from get_trace).
"""
import sys
import functools
import numbers

from udplistener import get_events_and_response, get_trace, _make_call

if sys.version_info < (2, 7):
    try:
        import unittest2 as unittest
    except ImportError:
        import unittest
else:
    import unittest


def factory(intercept=None):
    if intercept and intercept.upper() == 'FALSE':
        return TestWebInstrumentationPass
    else:
        return TestWebInstrumentation

class TestWebInstrumentation(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        self.base_url = None
        self.result = None
        unittest.TestCase.__init__(self, *args, **kwargs)

    def setUp(self):
        self.result = None

    def all_have(self, pred, events):
        return all(pred(ev) for ev in events)

    def some_have(self, pred, events):
        return any(pred(ev) for ev in events)

    def num_have(self, pred, events):
        num = 0
        for e in events:
            if pred(e):
                num += 1
        return num

    def none_have(self, pred, events):
        return self.all_have(lambda ev: not pred(ev), events)

    ###########################################################################
    # Assertions we make about many traces
    #
    # Not run by default, both for explicitness and flexibility.
    ###########################################################################

    def assert_single_xtrace(self, events):
        xtraces = set([ev.get('X-Trace')[:42] for ev in events])
        self.assertEquals(len(xtraces), 1,
                          "Did not find exactly one X-Trace header in this set of events. Found %s" % len(xtraces))
        self.assertNotEquals(xtraces, [None],
                             "Did not find a valid X-Trace header.")

    def assert_no_errors(self, events):
        self.assertTrue(self.none_have(lambda ev: ev['Label'] == 'error', events))

    def assert_has_errors(self, events):
        self.assertTrue(self.some_have(lambda ev: ev['Label'] == 'error' or 'ErrorClass' in ev, events))

    def assert_standard_trace(self, events):
        self.assert_no_errors(events)
        self.assert_single_xtrace(events)

    def assert_num_have(self, pred, events, num, msg=None):
        self.assertEquals(num, self.num_have(pred, events), msg)

    def assert_trace_layers(self, layers, trace):
        self.assertEquals([ex.layer() for ex in sorted(trace.all_extents())], layers)

    def assert_trace_layers_include(self, layers, trace):
        # check that actual trace layers is a superset of expect layers
        actual_layers = set([ex.layer() for ex in sorted(trace.all_extents())])
        expect_layers = set(layers)
        self.assertTrue(actual_layers >= expect_layers, '%s does not include %s' % (actual_layers, expect_layers))

    # different implementation from assert_trace_layers_include.
    # if trace contains A, B, C, ... Z, we want to know that A, E, and Q are SOMEWHERE
    # and in that order, but not necessarily "touching" in the list.
    def assert_trace_layers_include_ordered(self, layers, trace):
        trace_layers = [ex.layer() for ex in sorted(trace.all_extents())]
        start_index = 0
        try:
            for layer in layers:
                # will error out if layer isn't found
                start_index = trace_layers[start_index:len(trace_layers)].index(layer) + 1
        except ValueError:
            self.assertTrue(False, "Can't find layer %s at or after index %s" % (layer, start_index))

    def assert_descendant_layers(self, layers, trace, branch):
        self.assertEquals([ex.layer() for ex in self.get_descendant_layers(trace, branch)], layers)

    def assert_descendant_layers_include(self, layers, trace, branch):
        # check that actual trace branch layers is a superset of expected layers
        actual_branch_layers = set([ex.layer() for ex in self.get_descendant_layers(trace, branch)])
        expect_branch_layers = set(layers)
        self.assertTrue(actual_branch_layers >= expect_branch_layers,
                        '%s does not include %s' % (actual_branch_layers, expect_branch_layers))

    def assert_descendant_layers_alt(self, layers, trace):
        # given a sequence of (layername, allowed range) like:
        # (
        #  ('layer1', [0]),
        #  ('layer2', [0]),
        #  ('layer3', [0,1,2])
        # )
        # will check that the trace has:
        # layer1
        #       \
        #        layer2
        #              \
        #               foo | bar | layer3
        # where layer3 will be looked for in the allowed range of sibling branches
        current_extent = trace
        for layer, branch_range in layers:
            found = False
            # check each child branch within the allowed range
            for i in branch_range:
                if not 0 <= i < len(current_extent.extents):
                    break
                extent = current_extent.extents[i]
                if extent.layer() == layer:
                    found = True
                    current_extent = extent
                    break
            # fail if cannot find a child branch with specified layer name
            self.assertTrue(found, 'Cannot find %s within range %s in %s' %
                            (layer, branch_range, [ex.layer() for ex in current_extent.extents]))

    ###########################################################################
    # Helper functions for testing common invariants
    ###########################################################################

    def get_layer_names(self, events):
        return set([ev.get('Layer') for ev in events if ev.get('Layer') is not None])

    def get_profile_names(self, events):
        return set([ev.get('ProfileName') for ev in events])

    def filter_for(self, key, value, events):
        return filter(lambda ev: key in ev and ev[key] == value, events)

    def filter_events(self, pred, events):
        return [ev for ev in events if pred(ev)]

    def get_layer(self, value, events):
        return self.filter_for('Layer', value, events)

    def get_label(self, value, events):
        return self.filter_for('Label', value, events)

    def get_profile(self, value, events):
        return self.filter_for('ProfileName', value, events)

    def get_extent(self, pred, trace, sort_key=lambda ex: ex.start_time()):
        return sorted(trace.all_extents(pred), key=sort_key)

    def get_events_for_trace(self, trace_id, events):
        return self.filter_events(lambda ev: ev['X-Trace'][2:42] == trace_id, events)

    def get_trace_ids(self, pred, events):
        return [ev['X-Trace'][2:42] for ev in events if pred == None or pred(ev)]

    def get_non_init_events(self, events):
        inits = self.get_trace_ids(lambda ev: '__Init' in ev and ev['__Init'], events)
        return self.filter_events(lambda ev: ev['X-Trace'][2:42] not in inits, events)

    def get_non_jmx_events(self, events):
        jmxs = self.get_trace_ids(lambda ev: 'Layer' in ev and ev['Layer'] == 'JMX', events)
        return self.filter_events(lambda ev: ev['X-Trace'][2:42] not in jmxs, events)

    def get_descendant_layers(self, trace, branch):
        desc_layers = [trace.extents[branch]]
        extent = trace.extents[branch]
        while extent.extents:
            desc_layers.append(extent.extents[0])
            extent = extent.extents[0]
        return desc_layers

    ###########################################################################
    # Wrappers around udplisten
    ###########################################################################

    def get_trace(self, *args, **kwargs):
        """ current invariant: args[0] is a str, urllib2.Request instance, or callable() """
        if hasattr(self, 'base_url') and self.base_url != None and type(args[0]) == str:
            args = (self.base_url + args[0], ) + args[1:]
        return get_trace(*args, **kwargs)

    def get_events(self, url, *args, **kwargs):
        """ current invariant: url arg is a str, urllib2.Request instance, or callable(). Returns events only. """
        if hasattr(self, 'base_url') and self.base_url != None and type(url) == str:
            url = self.base_url + url
        events, _, response = get_events_and_response(url, *args, **kwargs)
        self.result = response
        return events

    def get_events_and_inits(self, url, *args, **kwargs):
        """ current invariant: url arg is a str, urllib2.Request instance, or callable().  Returns events and inits. """
        if hasattr(self, 'base_url') and self.base_url != None and type(url) == str:
            url = self.base_url + url
        events, inits, response = get_events_and_response(url, *args, **kwargs)
        self.result = response
        return events, inits

""" Override TestWebInstrumentation to exercise test URLs end-to-end.

The methods in this class just make requests to the url / callable then skip
the rest of the current test.
"""
class TestWebInstrumentationPass(TestWebInstrumentation):
    def __init__(self, *args, **kwargs):
        TestWebInstrumentation.__init__(self, *args, **kwargs)

    def _call_and_skip(self, url):
        func = url if callable(url) else functools.partial(_make_call, url)
        try:
            result = func()
        except Exception as e:
            result = e
        self.result = result
        self.skipTest('Not intercepting')

    def get_events_and_inits(self, url, *args, **kwargs):
        if hasattr(self, 'base_url') and self.base_url != None and type(url) == str:
            url = self.base_url + url
        self._call_and_skip(url)

    def get_events(self, url, *args, **kwargs):
        if hasattr(self, 'base_url') and self.base_url != None and type(url) == str:
            url = self.base_url + url
        self._call_and_skip(url)

    def get_trace(self, *args, **kwargs):
        if hasattr(self, 'base_url') and self.base_url != None and type(args[0]) == str:
            url = self.base_url + args[0]
        else:
            url = None
        self._call_and_skip(url)

class TestMetrics(unittest.TestCase):
    def assert_general_keys(self, metrics_message):
        """ Asserts general key are present in received metrics according to https://github.com/librato/trace/blob/master/docs/Collector-RPC-API.md#encoding
        Keyword arguments:
        metrics_message -- received metrics message to be assert on
        """
        self.assertIn('UnameSysName', metrics_message)
        self.assertIn('UnameVersion', metrics_message)
        self.assertIn('Distro', metrics_message)
        self.assertIn('IPAddresses', metrics_message)
        self.assertIn('MetricsFlushInterval', metrics_message)
        
    def assert_histograms(self, metrics_messages, expected_transaction_names):
        """ Asserts inbound histograms (with key 'TransactionResponseTime') according to https://github.com/librato/trace/blob/master/docs/specs/agent-metrics.md#histogram
        Each of the expected transaction name provided should at least appear in one of the metrics messages' historgram entries 
        
        Keyword arguments:
        metrics_messages -- an array of received metrics to be assert on
        expected_transaction_names - a set of transaction names that must appear in the histogram entries. For example {'/test-servlet/1', '/test-servlet/2'} 
        """
        #assert histograms
        histogram_transaction_names = set()
        for metric_message in metrics_messages:
            service_level_histogram_count = 0
            for histogram in metric_message['histograms']:
                self.assertEqual('TransactionResponseTime', histogram['name'])
                self.assertNotEqual(None, histogram['value'])
                if 'tags' not in histogram or not histogram['tags']:
                    service_level_histogram_count += 1
                else:
                    transaction_name = histogram['tags']['TransactionName']
                    self.assertNotEqual(None, transaction_name)
                    histogram_transaction_names.add(transaction_name)

            #assert service level histogram count
            self.assertEquals(1, service_level_histogram_count)
        
        
        #assert transaction names, be a bit relaxed as some app server might trigger some extra transaction names
        self.assertTrue(histogram_transaction_names.issuperset(expected_transaction_names))
        
    def assert_legacy_measurements(self, metrics_message):
        """ Asserts legacy measurements - heartbeats and request counters
        
        Keyword arguments:
        metrics_message -- received metrics message to be assert on
        """
        self._assert_heartbeat(metrics_message)
        self._assert_request_counters(metrics_message)
                     
    def _assert_request_counters(self, metrics):
        """ Asserts request counter according to https://github.com/tracelytics/culture/blob/master/product/traceview/specs/instrumentation/request_counters.md
        
        Keyword arguments:
        metrics -- received metrics to be assert on
        """
        request_count = self._get_measurement_value_by_name(metrics, 'RequestCount')
        self.assertTrue(isinstance(request_count, numbers.Number))
        self.assertTrue(request_count > 0)
        trace_count = self._get_measurement_value_by_name(metrics, 'TraceCount')
        self.assertTrue(isinstance(trace_count, numbers.Number))
        self.assertTrue(trace_count > 0)
        sample_count = self._get_measurement_value_by_name(metrics, 'SampleCount')
        self.assertTrue(isinstance(sample_count, numbers.Number))
        self.assertTrue(sample_count > 0)
        sample_rate = self._get_measurement_value_by_name(metrics, 'SampleRate')
        self.assertTrue(isinstance(sample_rate, numbers.Number))
        sample_source = self._get_measurement_value_by_name(metrics, 'SampleSource')
        self.assertTrue(isinstance(sample_source, numbers.Number))
    
    def _assert_heartbeat(self, metrics):
        """ Asserts heartbeat messages according to https://github.com/librato/trace/blob/master/docs/specs/heartbeat-message.md
        
        Keyword arguments:
        metrics -- received metrics to be assert on
        """   
        #TotalRAM, FreeRAM, ProcessLoad - but all those seem optional?
        num_sent = self._get_measurement_value_by_name(metrics, 'NumSent')
        self.assertTrue(num_sent > 0)
        self.assertTrue(isinstance(num_sent, numbers.Number))
        total_events = self._get_measurement_value_by_name(metrics, 'TotalEvents')
        self.assertTrue(isinstance(total_events, numbers.Number))
        self.assertTrue(total_events > 0)
        self.assertTrue(self._get_measurement_value_by_name(metrics, 'NumOverflowed') == 0)
        self.assertTrue(self._get_measurement_value_by_name(metrics, 'NumFailed') == 0)
        self.assertTrue(self._get_measurement_value_by_name(metrics, 'QueueLargest') >= 0)
    
    def assert_inbound_metrics(self, metrics_messages, expected_keys):
        """ Asserts inbound metrics measurements according to https://github.com/librato/trace/blob/master/docs/specs/agent-metrics.md#measurement
        Take note that this accepts an array of metrics message and the expected keys should be found in at least one of the metrics message
        Keyword arguments:
        metrics_messages -- an array of received metrics message to be assert on
        expected_keys - a set of frozenset of tags. For example {frozenset({('HttpMethod', 'GET'), ('TransactionName', '/test-servlet/5')}), frozenset({('TransactionName', '/test-servlet/4'), ('HttpStatus', '200')})}
                        collected metrics should at least contain measurement entries with keys in expected_keys
        """
        metrics_by_keys = {}
        for metric in metrics_messages:
            metrics_measurements = self._get_measurement_by_name(metric, 'TransactionResponseTime')
            for metrics_measurement in metrics_measurements:
                metrics_by_keys[frozenset(metrics_measurement['tags'].items())] = metrics_measurement
        
        received_keys = set(metrics_by_keys.keys())
        
        print("Expected keys: {0}".format(expected_keys))
        print("Received keys: {0}".format(received_keys))
        self.assertTrue(received_keys.issuperset(expected_keys), 'Keys do not match! Missing keys : {0}'.format(expected_keys.difference(received_keys)))
        
        for expected_key in expected_keys:
            metrics_measurement = metrics_by_keys[expected_key]
            self.assertEquals(1, metrics_measurement['count'])
            self.assertTrue(isinstance(metrics_measurement['sum'], numbers.Number))
        
    def assert_inbound_metrics_simple(self, metrics_messages, expected_transaction_names, expected_secondary_tags):
        """ Asserts inbound metrics measurements according to https://github.com/librato/trace/blob/master/docs/specs/agent-metrics.md#measurement 
        
        Take note that this is a convenient version with simpler parameters which should be sufficient if all the expected metrics share the same secondary tag key/values.
        
        This method accepts an array of received metrics message which the expected transaction name and secondary tags combo should be found in at least one of the message in the array
        
        Keyword arguments:
        metrics_messages -- an array of received metrics message to be assert on
        expected_transaction_names -- a set of transaction names that must appear in the inbound metrics measurement entries. For example {'/test-servlet/1', '/test-servlet/2'}
        expected_secondary_tags -- a map of tags that every expected metrics entry should contain within its tags field. For example {'HttpMethod':'GET', 'HttpStatus':'200'}
        """ 
        expected_keys = set()
        for expected_transaction_name in expected_transaction_names:
            expected_keys.add(frozenset({ 'TransactionName': expected_transaction_name}.items())) #key w/o any 2nd tags
            for expected_secondary_tag_key, expected_secondary_tag_value in expected_secondary_tags.items():
                expected_keys.add(frozenset({ 'TransactionName': expected_transaction_name, expected_secondary_tag_key: expected_secondary_tag_value}.items())) #key with each 2nd tags
        self.assert_inbound_metrics(metrics_messages, expected_keys)
                
    def _get_measurement_by_name(self, metrics, name):
        return [m for m in metrics['measurements'] if m['name'] == name]
    
    def _get_measurement_value_by_name(self, metrics, name):
        """ Assuming there's only one entry with simple "name"/"value" fields
        """
        return self._get_measurement_by_name(metrics, name)[0]['value']
