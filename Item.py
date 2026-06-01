import os
import json
from Globals import ONEDRIVE_DB_FOLDER, INCLUDE_EXCLUDE_FILE
from Globals import LOGGER as logger

def get_include_exclude_list():
    """Get the list of excluded folders/files"""
    # Check if exclude file exists, if not create it with proper structure
    if not os.path.exists(INCLUDE_EXCLUDE_FILE):
        # Create the directory if it doesn't exist
        os.makedirs(os.path.dirname(INCLUDE_EXCLUDE_FILE), exist_ok=True)
        # Create empty exclude file with proper structure
        default_structure = {
            "include": [],
            "exclude": []
        }
        with open(INCLUDE_EXCLUDE_FILE, "w") as file:
            json.dump(default_structure, file, indent=2)
        return [], []
    
    try:
        with open(INCLUDE_EXCLUDE_FILE, "r") as file:
            data = json.load(file)
            # Handle both old format (array) and new format (object)
            if isinstance(data, list):
                # Old format: convert to new format
                return [], data  # Assume old format was exclude-only
            else:
                # New format: extract include and exclude lists
                return data.get("include", []), data.get("exclude", [])
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        # If file is corrupted or doesn't exist, return empty lists
        return [], []

INCLUDE_LIST, EXCLUDE_LIST = get_include_exclude_list()

def get_etag_from_local(id):
    path = f'{ONEDRIVE_DB_FOLDER}/{id}'

    if os.path.isfile(path) is True: #file exists
        with open(path, "r") as itemfile:
            r = json.load(itemfile)
        return r["eTag"]
    else:
        return ""
    
def is_excluded(path):
    # Returns False if EXCLUDE_LIST is empty
    if not EXCLUDE_LIST:
        return False

    # Extract parent folder from path
    # OneDrive paths typically look like: /drive/root:/ParentFolder/subfolder/file.txt
    # We want to extract just the parent folder name
    parent_folder = extract_parent_folder(path)
    
    for e in EXCLUDE_LIST:
        exclude_pattern = e["path"]
        # Remove the ":/" prefix if present for comparison
        if parent_folder.startswith(exclude_pattern):
            return True
    return False

def is_included(path):
    """
    Returns True if the path is in the INCLUDE LIST
    """
    # Return True if INCLUDE_LIST is empty
    if not INCLUDE_LIST:
        return True

    # Extract parent folder from path
    parent_folder = extract_parent_folder(path)
    
    for e in INCLUDE_LIST:
        include_pattern = e["path"]

        if parent_folder.startswith(include_pattern):
            return True
    return False

def extract_parent_folder(path):
    """
    Extract the path starting from the colon
    Examples:
    - "/drive/root:/Pictures/subfolder/file.jpg" -> ":/Pictures/subfolder/file.jpg"
    - "/drive/root:/Documentos/file.pdf" -> ":/Documentos/file.pdf"
    - ":/Pictures/file.jpg" -> ":/Pictures/file.jpg"
    """
    # Handle different OneDrive path formats
    if ":" in path:
        # Find the colon and return everything from the colon onwards
        colon_index = path.find(":")
        return path[colon_index:]
    else:
        # If no colon found, return the original path
        return path
    

def get_download_status_message(status_code):
    """
    Get human-readable message for status code
    """
    messages = {
        1: "Should download",
        2: "Excluded by exclude list",
        3: "Not in include list", 
        4: "Invalid path or missing parent reference"
    }
    return messages.get(status_code, "Unknown status")

def should_include_path(path, is_file=True):
    """
    Determine if a path should be included based on include/exclude configuration.
    
    Logic:
    1. First check if path is explicitly excluded (recursive exclusion)
    2. Special case: Files in root directory are included by default (folders are not)
    3. If include list is empty, operate only by exclusion criteria
    4. If include list is not empty, path must be in include list to be included
    5. Recursive inclusion: if a parent path is included, all children are included
    
    Args:
        path (str): The path to check (e.g., ":/Pictures/vacation/photo.jpg")
        is_file (bool): True if this is a file, False if it's a folder
    
    Returns:
        bool: True if path should be included, False otherwise
    """
    # Extract the OneDrive path format (starting with :/)
    onedrive_path = extract_parent_folder(path)
    
    # 1. Check exclusion first (exclusion takes precedence)
    for exclude_item in EXCLUDE_LIST:
        exclude_path = exclude_item["path"]
        # Check if the path starts with the exclude pattern (recursive exclusion)
        if onedrive_path.startswith(exclude_path):
            # Make sure it's a proper path match (not just substring)
            if (onedrive_path == exclude_path or 
                onedrive_path.startswith(exclude_path + "/") or
                exclude_path == ":/"):  # Special case for root exclusion
                return False
    
    # 2. Special case: Files in root directory are included by default
    # Check if this is a file directly in the root (:/filename.ext)
    if is_file and onedrive_path.count("/") == 1 and onedrive_path.startswith(":/"):
        # This is a file in the root directory - include by default
        return True
    
    # 3. If include list is empty, operate only by exclusion criteria
    if not INCLUDE_LIST:
        return True  # Not excluded, so include it
    
    # 4. Check inclusion (path must be in include list to be included)
    for include_item in INCLUDE_LIST:
        include_path = include_item["path"]
        # Check if the path starts with the include pattern (recursive inclusion)
        if onedrive_path.startswith(include_path):
            # Make sure it's a proper path match (not just substring)
            if (onedrive_path == include_path or 
                onedrive_path.startswith(include_path + "/") or
                include_path == ":/"):  # Special case for root inclusion
                return True
    
    # 5. Path is not in include list
    return False

def should_download_v2(item):
    """
    Improved version using the new inclusion logic
    
    Returns:
        1 - Should download (included and not excluded)
        2 - Should not download (excluded)
        3 - Should not download (not in include list)
        4 - Should not download (no parent reference or invalid path)
    """
    # Check if item is valid
    if not item or "parentReference" not in item or "name" not in item:
        return 4
    
    # Extract the full path for checking
    if "path" in item["parentReference"]:
        full_path = f'{item["parentReference"]["path"]}/{item["name"]}'
    else:
        return 4
    
    # Determine if this is a file or folder
    is_file = "file" in item
    
    # Use the new inclusion logic
    if should_include_path(full_path, is_file):
        return 1
    else:
        # Determine if it was excluded or just not included
        onedrive_path = extract_parent_folder(full_path)
        
        # Check if it was explicitly excluded
        for exclude_item in EXCLUDE_LIST:
            exclude_path = exclude_item["path"]
            if (onedrive_path.startswith(exclude_path) and
                (onedrive_path == exclude_path or 
                 onedrive_path.startswith(exclude_path + "/") or
                 exclude_path == ":/")):
                return 2  # Excluded
        
        return 3  # Not in include list
    
def should_download_simple(item):
    """Simple version that returns boolean"""
    return should_download_v2(item) == 1