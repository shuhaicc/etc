
"""Test DjangoOrm instrumentation.

Runs tests for djangoware using helper server in separate process
"""
import hmac
import hashlib
from subprocess import Popen
import time
import socket

import sys
import os
import appoptics_apm

try:
    from . import base
except:
    sys.path.insert(0, os.path.abspath('.'))
    import base

try:
    from . import net_utils as tutil
except:
    sys.path.insert(0, os.path.abspath('.'))
    import net_utils as tutil

sys.modules['base'] = base
try:
    from . import trace_filters as filters
except:
    sys.path.insert(0, os.path.abspath('.'))
    import trace_filters as filters

try:
    import unittest
except ImportError:
    import unittest2 as unittest


SERVER_HELPER = None
SERVER_URL = "http://localhost:8000"
REQUEST_TIMEOUT = 5 # seconds
MAX_EVENTS = 20
BUFFER_SIZE = int(os.environ.get('UDPLISTEN_BUFFER_SIZE', 131071))

def simple_query(endpoint, query = None):
    """ simple http get query to get server status or
        invoke server side API
    """
    url = "{server}/{endpoint}".format(server=SERVER_URL, endpoint=endpoint)
    rc, _ = tutil.urllib_query(url, query)
    return rc == 200

def get_query_events(endpoint, query, timeout=1):
    """ simple API to get instrument events generated
        on local django server.
        :param url:str end point address
        :pram query:str sql query to execute on server
        :return event data returned from server
    """
    url = "{server}/{endpoint}".format(server=SERVER_URL, endpoint=endpoint)
    events = []
    spans = []
    response = "ERROR"

    with tutil.UdpCollector() as udp_collector:
        udp_collector.settimeout(timeout)
        full_data = []
        start_time = time.time()
        try:
            rc, query_data = tutil.urllib_query(url, query, REQUEST_TIMEOUT)
            if rc == 200:
                response = query_data.read()
        except Exception:
            pass

        while len(full_data) < MAX_EVENTS:
            try:
                ret = udp_collector.recv(BUFFER_SIZE)
                evt = tutil.unpickle_obj(ret)
                if isinstance(evt, appoptics_apm.appoptics_apm_test.Event):
                    events.append(evt)
                else:
                    spans.append(evt)

            except socket.timeout:
                break

    return events, spans, response, rc



def propertyStr(s):
    if isinstance(s, bytes):
        return s.decode()
    else:
        return s

class TestDjangoOrm(base.TraceTestCase):
    ''' Simple test case class and base of specific test cases class'''
    @classmethod
    def setUpClass(cls):
        global SERVER_HELPER
        SERVER_HELPER = Popen(["gunicorn", "-w", "1", "test.unit.mini_djapp"])
        time.sleep(3)
        while not simple_query(""):
            time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        global SERVER_HELPER
        SERVER_HELPER.terminate()


    def assertHasBaseEntryAndExit(self):
        self.print_events() # only really prints anything if test case fails
        self.assertEqual(1, len(self._last_trace.pop_events(base.f.is_entry_event, base.f.layer_is('django'))))
        self.assertEqual(1, len(self._last_trace.pop_events(base.f.is_exit_event, base.f.layer_is('django'))))

    def assertSaneTrace(self):
        self.assertHasBaseEntryAndExit()
        # for mysql, each time a query is executed, SELECT @@SQL_AUTO_IS_NULL
        # is executed. This is expected behavior for some versions if not all.
        # this will generate one extra 'djangoORM' layer for each query
        django_orm_entries = len(self._last_trace.pop_events(filters.is_entry_event, filters.layer_is('djangoORM')))
        self.assertIn(django_orm_entries, [1, 2])
        exit_event = self._last_trace.pop_events(filters.is_exit_event, filters.layer_is('djangoORM'))

        self.assertIn(len(exit_event), [1, 2])
        return exit_event[-1]

    def test_simple(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_simple', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))
        self.assertIn(exit_event.props.get('QueryArgs'), ['{}', '()', 'None', b'None'])

    def test_args(self):
        query = {'query':'SELECT 1 LIMIT %s', 'value':1}
        events, _, _, rc = get_query_events('test_args', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))

    @unittest.skip("under non-autocommit mode, sqlite has problem for cursor commit")
    def test_commit(self):
        #if self.flavor != 'sqlite':
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_commit', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.listener.get_events()
        nevents = 2
        self.assertEqual(nevents, len(exits))

        self.assertEqual(query, propertyStr(exits[1].props.get('Query')))
        self.assertEqual('COMMIT', propertyStr(exits[1].props.get('Query')))

    @unittest.skip("Cursor does not support explicit START TRANS, and It complains"
                   "'no transaction is active'")
    def test_rollback(self):
        #if self.flavor != "postgresql":
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_rollback', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.listener.get_events()
        nevents = 2
        self.assertEqual(nevents, len(exits))

        self.assertEqual(query, propertyStr(exits[1].props.get('Query')))
        self.assertEqual('ROLLBACK', propertyStr(exits[1].props.get('Query')))

    def test_remotehost_and_flavor(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_remotehost_and_flavor', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        trace_exit_event = self.assertSaneTrace()
        self.assertEqual('sqlite', propertyStr(trace_exit_event.props.get('Flavor')))

    def test_simple_postgres(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_simple_postgres', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))

        self.assertIn(exit_event.props.get('QueryArgs'), ['{}', '()', 'None', b'None'])

    def test_args_postgres(self):
        query = {'query':'SELECT 1 LIMIT %s', 'value':1}
        events, _, _, rc = get_query_events('test_args_postgres', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))

    def test_commit_postgres(self):
        query = {'query':'COMMIT'}
        events, _, _, rc = get_query_events('test_commit_postgres', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.pop_events(
            base.f.is_exit_event, base.f.layer_is('djangoORM'))
        nevents = 2
        self.assertEqual(nevents, len(exits))

        self.assertEqual(query['query'], propertyStr(exits[-1].props.get('Query')))

    @unittest.skip("Cursor does not support explicit START TRANS")
    def test_rollback_postgres(self):
        query = {'query':'ROLLBACK'}
        events, _, _, rc = get_query_events('test_rollback_postgres', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.listener.get_events()
        nevents = 2
        self.assertEqual(nevents, len(exits))

        self.assertEqual(query, propertyStr(exits[1].props.get('Query')))

    def test_remotehost_and_flavor_postgres(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_remotehost_and_flavor_postgres', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        trace_exit_event = self.assertSaneTrace()
        self.assertEqual('127.0.0.1', propertyStr(trace_exit_event.props.get('RemoteHost')))
        self.assertEqual('postgresql', propertyStr(trace_exit_event.props.get('Flavor')))

    def assertSaneTrace_mysql(self):
        self.assertHasBaseEntryAndExit()
        # for mysql, each time a query is executed, SELECT @@SQL_AUTO_IS_NULL
        # is executed. This is expected behavior for some versions if not all.
        # this will generate one extra 'djangoORM' layer for each query
        django_orm_entries = len(self._last_trace.pop_events(filters.is_entry_event, filters.layer_is('djangoORM')))
        self.assertIn(django_orm_entries, [1, 2, 3])
        exit_event = self._last_trace.pop_events(filters.is_exit_event, filters.layer_is('djangoORM'))

        self.assertIn(len(exit_event), [1, 2, 3])
        return exit_event[-1]

    def test_simple_mysql(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_simple_mysql', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace_mysql()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))

        self.assertIn(exit_event.props.get('QueryArgs'), ['{}', '()', 'None', b'None'])

    def test_commit_mysql(self):
        query = {'query':'COMMIT'}
        events, _, django_version, rc = get_query_events('test_commit_mysql', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.pop_events(
            base.f.is_exit_event, base.f.layer_is('djangoORM'))
        self.assertIn(len(exits), [2, 3])
        self.assertEqual(query['query'], propertyStr(exits[-1].props.get('Query')))

    def test_args_mysql(self):
        query = {'query':'SELECT 1 LIMIT %s', 'value':1}
        events, _, _, rc = get_query_events('test_args_mysql', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        exit_event = self.assertSaneTrace_mysql()
        self.assertEqual(query['query'], propertyStr(exit_event.props.get('Query')))

    def test_rollback_mysql(self):
        query = {'query':'ROLLBACK'}
        events, _, django_version, rc = get_query_events('test_rollback_mysql', query)
        self.assertEqual(rc, 200)
        # for some versions DjangoOrm entry events is 2, some is 3
        self.initialize_trace(events)
        self.assertHasBaseEntryAndExit()
        exits = self._last_trace.pop_events(
            base.f.is_exit_event, base.f.layer_is('djangoORM'))
        self.assertIn(len(exits), [2, 3])
        self.assertEqual(query['query'], propertyStr(exits[-1].props.get('Query')))

    def test_remotehost_and_flavor_mysql(self):
        query = {'query':'SELECT 1'}
        events, _, _, rc = get_query_events('test_remotehost_and_flavor_mysql', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        trace_exit_event = self.assertSaneTrace_mysql()
        self.assertEqual('127.0.0.1', propertyStr(trace_exit_event.props.get('RemoteHost')))
        self.assertEqual('mysql', propertyStr(trace_exit_event.props.get('Flavor')))

    def test_djangoware_transaction_name(self):
        query = {'query':'django_ware_transaction_name_test'}
        events, spans, _, rc = get_query_events('test_djangoware_transaction_name', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.initialize_spans(spans)
        span_report = self._last_trace.pop_spans(
                filters.has_span_prop('SPAN_REPORT', 'django_ware_transaction_name_test'))
        self.assertEqual(len(span_report), 1)

    def test_unicode_transaction_name(self):
        trans_name = appoptics_apm.util._apm_str(u'Lo\xefc Rodrigue-Gauthier')
        query = {'query': trans_name}
        events, spans, _, rc = get_query_events('test_unicode_transaction_name', query)
        self.assertEqual(rc, 200)
        self.initialize_trace(events)
        self.initialize_spans(spans)
        span_report = self._last_trace.pop_spans(
                filters.has_span_prop('SPAN_REPORT', appoptics_apm.util._apm_str(trans_name)))
        self.assertEqual(len(span_report), 1)

class TestDjangoMiddleSetting(TestDjangoOrm):
    ''' test Django middleware setting as MIDDLEWARE_CLASSES as tuple'''
    @classmethod
    def setUpClass(cls):
        global SERVER_HELPER
        process_env = os.environ.copy()
        process_env['TEST_MIDDLEWARE_CLASSES_TUPLE'] = True

        SERVER_HELPER = Popen(["gunicorn", "-w", "1", "test.unit.mini_djapp"])
        time.sleep(3)
        while not simple_query(""):
            time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        global SERVER_HELPER
        SERVER_HELPER.terminate()

if __name__ == '__main__':
    unittest.main()
