import json
import os
import requests
from requests.api import request
from Authentication import get_bearer_auth_header
from Utils import pretty_json
from Config import save_item_remoteinfo_to_db
from Item import get_etag_from_local
from Globals import ONEDRIVE_ROOT

def get_drive_information():
    url = "https://graph.microsoft.com/v1.0/me/drives"

    auth_header = get_bearer_auth_header()

    print(auth_header)
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    print(pretty_json(jsonResponse))


def sync_onedrive_to_disk(onedrive_root_folder, local_path, next_link=None):
    if next_link is None:
        url = "https://graph.microsoft.com/v1.0/me/drive/root/delta"
    else:
        url = next_link

    print(next_link)
    
    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    for item in jsonResponse["value"]:
        # print(pretty_json(item))

        if "folder" in item:
            #print("Folder " + item["name"] + " " + item["id"] + " " + item["parentReference"]["path"].split(":")[1])
            if "parentReference" in item and "path" in item["parentReference"]:
                sync_onedrive_to_disk_folder(item["name"], item["parentReference"]["path"].split(":")[1])
        if "package" in item:
            if "type" in item["package"] and item["package"]["type"] == "oneNote":
                sync_onedrive_to_disk_folder(item["name"], item["parentReference"]["path"].split(":")[1])
                
        if "file" in item:
            print("File " + item["name"] + " " + item["id"])
            if item["eTag"] == get_etag_from_local(item["id"]): # Skip download
                print(f'Skiping file {item["name"]} with id {item["id"]}')
            else: # Download and replace existing
                sync_onedrive_to_disk_file(item["name"], item["parentReference"]["path"].split(":")[1], item["@microsoft.graph.downloadUrl"])

        save_item_remoteinfo_to_db(item["id"], item)
    
    if "@odata.nextLink" in jsonResponse:
        sync_onedrive_to_disk(onedrive_root_folder, local_path, next_link=jsonResponse["@odata.nextLink"])
            
            
def sync_onedrive_to_disk_folder(folder_name, path):
    folder_full_path = f'{ONEDRIVE_ROOT}{path}/{folder_name}'
    
    if os.path.exists(folder_full_path) is False:
        os.mkdir(folder_full_path)
        
def sync_onedrive_to_disk_file(file_name, path, url):
    file_full_path = f'{ONEDRIVE_ROOT}{path}/{file_name}'
    
    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)
    
    with open(file_full_path, 'wb') as f:
        f.write(r.content)
    
    
        
    
