from setuptools import setup, find_packages

setup(
    name='convert_gpt_answer',
    version='2.2',
    packages=find_packages(),
    install_requires=[
        'json-repair',
        'num2words',
        'pillow',
    ],
)
