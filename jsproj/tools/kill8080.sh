#!/bin/bash
netstat -lpnt | grep 8080 | awk '{print $7}' | cut -d/ -f1  | xargs kill -9
