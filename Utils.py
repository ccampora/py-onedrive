import json

def pretty_json(ugly_json):
    return json.dumps(ugly_json, indent=2)

    """Returns the folder portion of the path
    """
def get_folder_from_path(path):
    return path[path.index(":") + 1:]