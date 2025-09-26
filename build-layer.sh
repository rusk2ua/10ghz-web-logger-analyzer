#!/bin/bash

# Build Python dependencies layer for Lambda
echo "Building Python dependencies layer..."

# Create layer directory structure
mkdir -p lambda/layers/python/python

# Install dependencies
pip install -r lambda/layers/python/requirements.txt -t lambda/layers/python/python/

echo "Layer build complete!"