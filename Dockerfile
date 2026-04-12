# Use official Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /code

# Install system dependencies required by mysqlclient.
# mysqlclient is a C extension that must be compiled against the MySQL client
# library. The slim base image does not include these build tools, so they
# must be installed before pip install runs:
#   default-libmysqlclient-dev  - MySQL client headers and shared library
#   pkg-config                  - lets the build system locate the library
#   gcc                         - C compiler needed to compile the extension
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    pkg-config \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt /code/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project
COPY . /code/

