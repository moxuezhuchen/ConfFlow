"""ConfFlow 打包安装配置（开发用）"""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="confflow",
    version="1.0",
    author="ConfFlow Team",
    author_email="team@confflow.org",
    description="Automated molecular conformation search workflow engine",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/user/confflow",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Chemistry",
    ],
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.20.0",
        "scipy>=1.7.0",
        "rdkit>=2021.09.0",
        "numba>=0.55.0",
        "tqdm>=4.60.0",
        "pyyaml>=5.4.0",
        "psutil>=5.8.0",
        "jinja2>=3.0.0",
        "rich>=10.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=6.0.0",
            "pytest-cov>=2.12.0",
            "black>=21.0.0",
            "flake8>=3.9.0",
            "mypy>=0.900",
            "sphinx>=4.0.0",
        ],
        "viz": [
            "matplotlib>=3.3.0",
            "plotly>=5.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "confflow=confflow.main:main",
            "confts=confflow.confts:main",
            "confgen=confflow.blocks.confgen:main",
            "confrefine=confflow.blocks.refine:main",
            "confcalc=confflow.calc.manager:main",
        ],
    },
    include_package_data=True,
    package_data={
        "confflow": ["*.yaml", "*.md"],
    },
)
