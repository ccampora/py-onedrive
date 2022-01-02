import json

def pretty_json(ugly_json):
    return json.dumps(ugly_json, indent=2)