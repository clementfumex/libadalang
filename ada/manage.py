#! /usr/bin/env python

from __future__ import absolute_import, division, print_function

import os.path
import subprocess
import sys

# Set the environment
from env import setenv
setenv()

from langkit.libmanage import ManageScript
from langkit.utils import Colors, printcol


class Manage(ManageScript):

    ENABLE_WARNINGS_DEFAULT = True

    def __init__(self):
        super(Manage, self).__init__()

        ########
        # Test #
        ########

        self.test_parser = test_parser = self.subparsers.add_parser(
            'test', help=self.do_test.__doc__
        )
        test_parser.add_argument(
            '--with-gnatpython', '-g', action='store_true',
            dest='with_gnatpython', default=False,
            help='Try to use GNATpython in the testsuite'
        )
        test_parser.add_argument(
            '--without-gnatpython', '-G', action='store_false',
            dest='with_gnatpython',
            help='Do not use GNATpython in the testsuite'
        )
        test_parser.add_argument(
            'testsuite-args', nargs='*',
            help='Arguments to pass to testsuite.py.'
        )
        test_parser.set_defaults(func=self.do_test)

        #############
        # Perf Test #
        #############

        self.perf_test_parser = perf_test_parser = self.subparsers.add_parser(
            'perf-test', help=self.do_perf_test.__doc__
        )
        perf_test_parser.add_argument(
            '--work-dir', default='performance_testsuite',
            help='Directory into which the performance testsuite will be'
                 ' executed'
        )
        perf_test_parser.add_argument(
            '--nb-runs', type=int, default=4,
            help='Number of runs (default: 4)'
        )
        perf_test_parser.add_argument(
            '--no-recompile', action='store_true',
            help='Do not recompile the library before running the perf'
                 ' testsuite'
        )
        perf_test_parser.set_defaults(func=self.do_perf_test)
        self.add_generate_args(perf_test_parser)
        self.add_build_args(perf_test_parser)

        ############
        # Make doc #
        ############

        self.make_doc_parser = make_doc_parser = self.subparsers.add_parser(
            'make-doc', help=self.make_doc.__doc__
        )
        make_doc_parser.set_defaults(func=self.make_doc)

    def create_context(self, args):
        # Keep these import statements here so that they are executed only
        # after the coverage computation actually started.
        from langkit.compile_context import CompileCtx, LibraryEntity
        from language.lexer import ada_lexer
        from language.grammar import ada_grammar
        from language.documentation import libadalang_docs

        return CompileCtx(
            lang_name='Ada',
            short_name='LAL',
            lexer=ada_lexer,
            grammar=ada_grammar,
            default_charset='iso-8859-1',
            verbosity=args.verbosity,
            env_hook_subprogram=LibraryEntity(
                'Libadalang.Unit_Files.Env_Hook',
                'Env_Hook'
            ),
            default_unit_provider=LibraryEntity(
                'Libadalang.Unit_Files.Default',
                'Default_Unit_Provider'
            ),
            symbol_canonicalizer=LibraryEntity('Libadalang.Sources',
                                               'Canonicalize'),
            documentations=libadalang_docs,
        )

    @property
    def main_source_dirs(self):
        return super(Manage, self).main_source_dirs | {
            os.path.join('testsuite', 'ada')
        }

    @property
    def main_programs(self):
        return super(Manage, self).main_programs | {'symres', 'navigate'}

    def do_test(self, args):
        """
        Run the testsuite.

        This is a wrapper around testsuite/testsuite.py tuned for interactive
        use: it correctly setups the environment according to the build
        directory, it enables colored output and it displays test outputs on
        error.
        """
        self.set_context(args)

        # Make builds available from testcases
        env = self.derived_env()

        if not args.with_gnatpython:
            env[b'WITHOUT_GNATPYTHON'] = b'1'

        argv = [
            'python',
            self.dirs.lang_source_dir('testsuite', 'testsuite.py'),
            '--enable-color', '--show-error-output',
        ]
        if not args.enable_shared:
            argv.append('--disable-shared')
        argv.extend(getattr(args, 'testsuite-args'))

        try:
            self.check_call(args, 'Testsuite', argv, env=env)
        except subprocess.CalledProcessError as exc:
            print('Testsuite failed: {}'.format(exc), file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            # At this point, the testsuite already made it explicit we stopped
            # after a keyboard interrupt, so we just have to exit.
            sys.exit(1)

    @staticmethod
    def _mkdir(path):
        """
        Create a new directory at `path` if it does not exist.

        :param path: the path to the new directory.
        :type path: str
        :raise: OSError | IOError
        """

        if os.path.isdir(path):
            return
        if os.path.exists(path):
            raise IOError('{}: already exists'.format(path))
        os.makedirs(path)

    @staticmethod
    def _find_ada_sources(work_dir):
        """
        Return the list of .adb and .ads files in `work_dir`.

        :param work_dir: the directory in which to search for ada sources.
        :type work_dir: str
        :rtype: set[str]
        """
        ada_files = set()
        for root, dirs, files in os.walk(work_dir):
            for filename in files:
                _, ext = os.path.splitext(filename)
                if ext in ('.ads', '.adb'):
                    ada_files.add(os.path.join(root, filename))
        return ada_files

    def make_doc(self, args):
        """
        Make the documentation for both langkit and libadalang.
        """
        subprocess.check_call([
            "python", self.dirs.lang_source_dir("doc", "generate_changelog.py")
        ])
        subprocess.check_call(
            ["make", "html"], cwd=self.dirs.lang_source_dir("doc")
        )
        subprocess.check_call(
            ["make", "html"], cwd=self.dirs.langkit_source_dir("..", "doc")
        )

    def do_perf_test(self, args):
        """
        Run the performance regression testsuite.
        """
        from time import time

        self.set_context(args)

        def file_lines(filename):
            with open(filename) as f:
                return len(list(f))

        work_dir = os.path.abspath(args.work_dir)
        args.build_dir = os.path.join(work_dir, args.build_dir)

        if not args.no_recompile:
            # The perf testsuite only needs the "parse" main program
            args.disable_mains = self.main_programs - {'parse'}

            # Build libadalang in production mode inside of the perf testsuite
            # directory.
            self.dirs.set_build_dir(args.build_dir)
            args.build_mode = 'prod'
            self._mkdir(args.build_dir)
            self.do_make(args)

        # Checkout the code bases that we will use for the perf testsuite
        source_dir = os.path.join(work_dir, "source")
        try:
            os.mkdir(source_dir)
        except OSError:
            pass
        os.chdir(source_dir)
        if not os.path.exists('gnat'):
            subprocess.check_call([
                'svn', 'co',
                'svn+ssh://svn.us.adacore.com/Dev/trunk/gnat',
                '-r', '314163',
                '--ignore-externals'
            ])
        if not os.path.exists('gps'):
            subprocess.check_call(['git', 'clone',
                                   'ssh://review.eu.adacore.com:29418/gps'])
        os.chdir('gps')
        subprocess.check_call(['git', 'checkout',
                               '00b73897a867514732d48ae1429faf97fb07ad7c'])
        os.chdir('..')

        # Make a list of every ada file

        # Exclude some files that are contained here but that we do not parse
        # correctly.
        excluded_patterns = ['@', 'a-numeri', 'rad-project']
        ada_files = filter(
            lambda f: all(map(lambda p: p not in f, excluded_patterns)),
            self._find_ada_sources(source_dir)
        )
        file_list_name = 'ada_file_list'
        with open(file_list_name, 'w') as file_list:
            for f in ada_files:
                file_list.write(f + '\n')

        # Get a count of the total number of ada source lines
        lines_count = sum(map(file_lines, ada_files))

        printcol("=================================", Colors.HEADER)
        printcol("= Performance testsuite results =", Colors.HEADER)
        printcol("=================================", Colors.HEADER)
        elapsed_list = []
        parse_args = ['{}/bin/parse'.format(args.build_dir), '-s', '-F',
                      file_list_name]
        for _ in range(args.nb_runs):
            # Execute parse on the file list and get the elapsed time
            t = time()
            subprocess.check_call(parse_args)
            elapsed = time() - t
            elapsed_list.append(elapsed)

            # Print a very basic report
            print("Parsed {0} lines of Ada code in {1:.2f} seconds".format(
                lines_count, elapsed
            ))

        print('')
        printcol('= Performance summary =', Colors.OKGREEN)
        print('Mean time to parse {0} lines of code : {1:.2f} seconds'.format(
            lines_count, sum(elapsed_list) / float(len(elapsed_list))
        ))


if __name__ == '__main__':
    Manage().run()
