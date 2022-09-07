# voyager_python_client
Python client for interacting with the Voyager Astrophotography application server

voyager_api contains the VoyagerClient class, and contains (most) interactions with the Voyager API for sending commands, as well as the ability to add handlers for specific published messages as well. Example can be seen in the ws_server.py file.

ws_server is a websocket bridge that takes updates from the Voyager dashboard client messages, and formats them into a json structure to pass to any connected clients.
