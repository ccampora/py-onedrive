# py-onedrive
Onedrive client made in Python (for educ purposes)

# Supported Features

* Authentication
* Sync onedrive to disk (one-way-direcction)
* Sync files and folder deletion from onedrive to disk (one-way-direcction)
* Exclude folder tree
* OneDrive Personal Only

# Installation

`git clone https://github.com/ccampora/py-onedrive.git`

# Configuration

There is no need for further configuration. The default location to sync is $HOME/onedrive. Make sure that folder exists. 

In order to use the Include / Exclude functionality: in $HOME/.py-onedrive create the file .py-onedrive-folders and include or exclude folders as needed. By default it will always include files (only) in the root directory

Format Example: 
```
{
	"include": [
                { "path": ":/Documents" }
        ],
	"exclude": [
		{ "path": ":/Pictures" },
    		{ "path": ":/Others/01" }
	]
}
```

# Run 

go into the cloned py-onedrive folder and run 

`python Onedrive.py`

the first time it will require you to login into your Microsoft Account. If the auth is successful you will be redirected to another URL. Copy the value of `code` into the console

`https://login.microsoftonline.com/common/oauth2/nativeclient?code={copy this}`

# Run on crontab

As for now the client is executed asynchronously. To run it on a recurrent fashion use a crontab
Edit user crontab
```
crontab -u <your user> -e
```
Add a new entry to run each minute
```
* * * * * python <path to cloned folder>/Onedrive.py >> $HOME/.py-onedrive/py-onedrive.log
```


