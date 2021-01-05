from setuptools import setup, find_packages

with open('Readme.md') as f:
    readme = f.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='radiotracking',
    version='0.1.0',
    description='Detect signals of wildlife tracking systems',
    long_description=readme,
    author='Jonas Höchst',
    author_email='hello@jonashoechst.de',
    url='https://github.com/nature40/pyradiotracking',
    install_requires=requirements,
    license=license,
    packages=find_packages(exclude=('tests', 'docs')),
)
