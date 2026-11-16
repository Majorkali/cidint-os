"""CIDINT OS desktop: login screen, file manager, settings and assistant.

The graphical programs run as unprivileged users. Everything that needs
root (checking a password, starting a session, changing a password,
restarting) goes through cidint-authd, which checks who is asking.
"""
