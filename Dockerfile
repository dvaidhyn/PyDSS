FROM python:3.8.2-slim

RUN apt-get update

# Copy the source code
COPY PyDSS /PyDSS

# Change directory to the src folder
WORKDIR /PyDSS

# Install the python modules
RUN pip install -e .

ENV PYTHONPATH=/PyDSS/PyDSS

EXPOSE 5000/tcp
EXPOSE 9090/tcp

# Change directory to the src folder
CMD [ "pydss", "serve" ]
