# Configuration file for jupyter-notebook.

c = get_config()  #noqa

## The IP address the notebook server will listen on.
#  Default: 'localhost'
c.NotebookApp.ip = '*'

## Whether to open in a browser after starting.
#                          The specific browser used is platform dependent and
#                          determined by the python standard library `webbrowser`
#                          module, unless it is overridden using the --browser
#                          (NotebookApp.browser) configuration option.
#  Default: True
c.NotebookApp.open_browser = False

## Token used for authenticating first-time connections to the server.
#
#          The token can be read from the file referenced by JUPYTER_TOKEN_FILE or set directly
#          with the JUPYTER_TOKEN environment variable.
#
#          When no password is enabled,
#          the default is to generate a new, random token.
#
#          Setting to an empty string disables authentication altogether, which
#  is NOT RECOMMENDED.
#  Default: '<generated>'
c.NotebookApp.token = ''

## Hashed password to use for web authentication.
#
#                        To generate, type in a python/IPython shell:
#
#                          from notebook.auth import passwd; passwd()
#
#                        The string should be of the form type:salt:hashed-
#  password.
#  Default: ''
c.NotebookApp.password = 'argon2:$argon2id$v=19$m=10240,t=10,p=8$pxR1iI8cyn87MljOflduiw$KHjnt+fiQTvuLkJc7pduF1IoK8p1MVFaAwcJf9eINRw'

## Forces users to use a password for the Notebook server.
#                        This is useful in a multi user environment, for instance when
#                        everybody in the LAN can access each other's machine through ssh.
#
#                        In such a case, serving the notebook server on localhost is not secure
#                        since any user can connect to the notebook server via ssh.
#  Default: False
c.NotebookApp.password_required = True

## The port the notebook server will listen on (env: JUPYTER_PORT).
#  Default: 8888
c.NotebookApp.port = 8888

## Whether to allow the user to run the server as root.
#  Default: False
c.NotebookApp.allow_root = True
