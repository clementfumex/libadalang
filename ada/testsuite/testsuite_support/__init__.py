from __future__ import absolute_import, division, print_function

import os

# pyflakes off
with_gnatpython = False
if not os.environ.get('WITHOUT_GNATPYTHON'):
    try:
        from gnatpython.testsuite import Testsuite as BaseTestsuite
    except ImportError:
        pass
    else:
        with_gnatpython = True
if not with_gnatpython:
    from testsuite_support.polyfill import BaseTestsuite
# pyflakes on


import testsuite_support.adaapi_driver
import testsuite_support.capi_driver
import testsuite_support.discriminants as discriminants
import testsuite_support.name_resolution_driver
import testsuite_support.navigation_driver
import testsuite_support.ocaml_driver
import testsuite_support.parser_driver
import testsuite_support.python_driver


class Testsuite(BaseTestsuite):
    TEST_SUBDIR = 'tests'
    DRIVERS = {
        'ada-api': testsuite_support.adaapi_driver.AdaAPIDriver,
        'c-api': testsuite_support.capi_driver.CAPIDriver,
        'navigation': testsuite_support.navigation_driver.NavigationDriver,
        'ocaml': testsuite_support.ocaml_driver.OCamlDriver,
        'parser': testsuite_support.parser_driver.ParserDriver,
        'python': testsuite_support.python_driver.PythonDriver,
        'name-resolution':
            testsuite_support.name_resolution_driver.NameResolutionDriver,
    }

    def add_options(self):
        # Depending on the testsuite framework involved (gnatpython or
        # polyfill), the option handling backend will be based either on
        # optparse or argparse. Both have different behaviors regarding
        # store_true options: we need to add default=False to canonicalize the
        # result of arguments parsing.
        self.main.add_option(
            '--discriminants',
            help='Comma-separated list of additional discriminants')
        self.main.add_option(
            '--valgrind', action='store_true', default=False,
            help='Run tests within Valgrind to check memory issues.')
        self.main.add_option(
            '--disable-shared', action='store_true', default=False,
            help='Disable tests involving shared libraries.')
        self.main.add_option(
            '--disable-python', action='store_true', default=False,
            help='Disable tests involving the Python API.')
        self.main.add_option(
            '--with-ocaml-bindings', default=None,
            help='If provided, must be a path from the current directory to'
                 ' the directory in which the OCaml bindings were generated.'
                 ' This enables tests involving the OCaml API (they are'
                 ' disabled by default).')
        self.main.add_option(
            '--with-python', default=None,
            help='If provided, use as the Python interpreter in testcases.')
        self.main.add_option(
            '--skip-internal-tests', action='store_true', default=False,
            help='Skip tests from the internal testsuite')

        #
        # Convenience options for developpers
        #

        # Debugging
        self.main.add_option(
            '--debug', '-g', action='store_true',
            help='Run a test under a debugger'
        )
        self.main.add_option(
            '--debugger', '-G', default='gdb',
            help='Program to use as a debugger (default: gdb)'
        )

        # Tests update
        self.main.add_option(
            '--rewrite', '-r', action='store_true',
            help='Rewrite test baselines according to current output.'
        )

    def tear_up(self):
        super(Testsuite, self).tear_up()

        opts = self.global_env['options']

        discriminants.add_discriminants(opts.discriminants)

        assert not opts.valgrind or not opts.debug, (
            'Debugging while checking memory with Valgrind is not supported.')

        ocaml_bindings = opts.with_ocaml_bindings
        self.global_env['ocaml_bindings'] = (
            os.path.abspath(ocaml_bindings) if ocaml_bindings else None
        )

    def write_comment_file(self, _):
        with open(os.path.join(self.output_dir, 'discr'), 'w') as f:
            f.write('Discriminants: {}'.format(
                ' '.join(discriminants.get_discriminants())))
