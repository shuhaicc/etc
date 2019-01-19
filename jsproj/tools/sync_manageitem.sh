#!/bin/bash

set +x

rm  ~/projs/IM/online_market_service/public/itemanage.html
#rm  ~/projs/IM/online_market_service/public/static/css/*
#rm  ~/projs/IM/online_market_service/public/static/js/*

cp  ~/projs/IM/manageitem/dist/index.html ~/projs/IM/online_market_service/public/itemanage.html
cp  ~/projs/IM/manageitem/dist/index.html ~/projs/IM/online_market_service/public/itemanage.html
cp  ~/projs/IM/manageitem/dist/static/css/* ~/projs/IM/online_market_service/public/static/css/
cp  ~/projs/IM/manageitem/dist/static/js/* ~/projs/IM/online_market_service/public/static/js/

