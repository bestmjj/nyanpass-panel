from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("src/requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh.readlines() if line.strip() and not line.startswith("#")]

setup(
    name="nyanpass-panel",
    version="1.0.0",
    author="Nyanpass",
    author_email="example@nyanpass.com",
    description="A web panel for monitoring and managing Nyanpass services",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/bestmjj/python/nyanpass-panel",
    project_urls={
        "Bug Tracker": "https://github.com/bestmjj/python/nyanpass-panel/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.6",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "nyanpass-panel=nyanpass_panel.app:NyanpassPanel",
        ],
    },
)