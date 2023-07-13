#!/bin/bash

PACKAGES="cachetools jsonpickle requests"
ALL_PACKAGES="cachetools certifi charset_normalizer idna jsonpickle requests urllib3"

set -e

rm -rf venv $ALL_PACKAGES

python3 -m venv ./venv

source ./venv/bin/activate

yes | pip3 install $PACKAGES

for i in $ALL_PACKAGES
do
  cp -r venv/lib/python3.10/site-packages/$i .
done

pip3 uninstall -y $ALL_PACKAGES
