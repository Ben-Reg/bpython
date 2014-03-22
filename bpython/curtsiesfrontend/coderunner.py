"""For running Python code that could interrupt itself at any time

in order to, for example, ask for a read on stdin, or a write on stdout

The CodeRunner spawns a greenlet to run code it, and that code can suspend
its own execution to ask the main thread to refresh the display or get information.
"""

import code
import signal
import sys
import greenlet
import logging

class SigintHappened(object):
    """If this class is returned, a SIGINT happened while the main thread"""

class SystemExitFromCodeThread(SystemExit):
    """If this class is returned, a SystemExit happened while in the code thread"""
    pass

class CodeRunner(object):
    """Runs user code in an interpreter.

    Running code requests a refresh by calling refresh_and_get_value(), which
    suspends execution of the code and switches back to the main thread

    After load_code() is called with the source code to be run,
    the run_code() method should be called to start running the code.
    The running code may request screen refreshes and user input
    by calling the refresh_and_get_value and wait_and_get_value calls
    respectively. When these are called, the running source code cedes
    control, and the current run_code() method call returns.

    The return value of run_code() determines whether the method ought
    to be called again to complete execution of the source code.

    Once the screen refresh has occurred or the requested user input
    has been gathered, run_code() should be called again, passing in any
    requested user input. This continues until run_code returns 'done'.

    Question: How does the caller of run_code know that user input ought
    to be returned?
    """
    def __init__(self, interp=None, stuff_a_refresh_request=lambda:None):
        """
        interp is an interpreter object to use. By default a new one is
        created.

        stuff_a_refresh_request is a function that will be called each time
        the running code asks for a refresh - to, for example, update the screen.
        """
        self.interp = interp or code.InteractiveInterpreter()
        self.source = None
        self.main_greenlet = greenlet.getcurrent()
        self.code_greenlet = None
        self.stuff_a_refresh_request = stuff_a_refresh_request
        self.code_is_waiting = False # waiting for response from main thread
        self.sigint_happened = False
        self.orig_sigint_handler = None

    @property
    def running(self):
        """Returns greenlet if code has been loaded greenlet has been started"""
        return self.source and self.code_greenlet

    def load_code(self, source):
        """Prep code to be run"""
        assert self.source is None, "you shouldn't load code when some is already running"
        self.source = source
        self.code_greenlet = None

    def _unload_code(self):
        """Called when done running code"""
        self.source = None
        self.code_greenlet = None
        self.code_is_waiting = False

    def run_code(self, for_code=None):
        """Returns Truthy values if code finishes, False otherwise

        if for_code is provided, send that value to the code greenlet
        if source code is complete, returns "done"
        if source code is incomplete, returns "unfinished"
        """
        if self.code_greenlet is None:
            assert self.source is not None
            self.code_greenlet = greenlet.greenlet(self._blocking_run_code)
            self.orig_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self.sigint_handler)
            request = self.code_greenlet.switch()
        else:
            assert self.code_is_waiting
            self.code_is_waiting = False
            signal.signal(signal.SIGINT, self.sigint_handler)
            if self.sigint_happened:
                self.sigint_happened = False
                request = self.code_greenlet.switch(SigintHappened)
            else:
                request = self.code_greenlet.switch(for_code)

        if request in ['wait', 'refresh']:
            self.code_is_waiting = True
            if request == 'refresh':
                self.stuff_a_refresh_request()
            return False
        elif request in ['done', 'unfinished']:
            self._unload_code()
            signal.signal(signal.SIGINT, self.orig_sigint_handler)
            self.orig_sigint_handler = None
            return request
        elif request in ['SystemExit']: #use the object?
            self._unload_code()
            raise SystemExitFromCodeThread()
        else:
            raise ValueError("Not a valid value from code greenlet: %r" % request)

    def sigint_handler(self, *args):
        """SIGINT handler to use while code is running or request being fufilled"""
        if greenlet.getcurrent() is self.code_greenlet:
            logging.debug('sigint while running user code!')
            raise KeyboardInterrupt()
        else:
            logging.debug('sigint while fufilling code request sigint handler running!')
            self.sigint_happened = True

    def _blocking_run_code(self):
        try:
            unfinished = self.interp.runsource(self.source)
        except SystemExit:
            return 'SystemExit'
        return 'unfinished' if unfinished else 'done'

    def wait_and_get_value(self):
        """Return the argument passed in to .run_code(for_code)

        Nothing means calls to run_code must be...
        """
        value = self.main_greenlet.switch('wait')
        if value is SigintHappened:
            raise KeyboardInterrupt()
        return value

    def refresh_and_get_value(self):
        """Returns the argument passed in to .run_code(for_code) """
        value = self.main_greenlet.switch('refresh')
        if value is SigintHappened:
            raise KeyboardInterrupt()
        return value

class FakeOutput(object):
    def __init__(self, coderunner, on_write):
        self.coderunner = coderunner
        self.on_write = on_write
    def write(self, *args, **kwargs):
        self.on_write(*args, **kwargs)
        return self.coderunner.refresh_and_get_value()
    def writelines(self, l):
        for s in l:
            self.write(s)
    def flush(self):
        pass
    def isatty(self):
        return True

def test_simple():
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    c = CodeRunner(stuff_a_refresh_request=lambda: orig_stdout.flush() or orig_stderr.flush())
    stdout = FakeOutput(c, orig_stdout.write)
    sys.stdout = stdout
    c.load_code('1 + 1')
    c.run_code()
    c.run_code()
    c.run_code()

def test_exception():
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    c = CodeRunner(stuff_a_refresh_request=lambda: orig_stdout.flush() or orig_stderr.flush())
    def ctrlc():
        raise KeyboardInterrupt()
    stdout = FakeOutput(c, lambda x: ctrlc())
    sys.stdout = stdout
    c.load_code('1 + 1')
    c.run_code()

if __name__ == '__main__':
    test_simple()
