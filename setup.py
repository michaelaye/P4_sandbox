import ez_setup
ez_setup.use_setuptools()
import sys
from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand

pandas_version = '0.13.1'

class PyTest(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = ['-v']
        self.test_suite = True

    def run_tests(self):
        #import here, cause outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.test_args)
        sys.exit(errno)


setup(
    name = "Planet4",
    version = "0.1beta1",
    packages = find_packages(),

    install_requires = ['pandas>='+pandas_version],
    tests_require = ['pytest'],

    cmdclass = {'test': PyTest},

    #metadata
    author = "K.-Michael Aye",
    author_email = "kmichael.aye@gmail.com",
    description = "Software for the reduction and analysis of Planet4 data.",
    license = "BSD 2-clause",
    keywords = "Mars Planet4 Zooniverse",
    url = "http:www.planetfour.org",
)