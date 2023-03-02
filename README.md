# GitHub notifications viewer

Show *unread* GitHub notifications for pull requests with extra context.

## Install

```shell
python -m venv venv
venv/bin/python -m pip install -e .
```

You also need to have the GitHub CLI installed and authenticated.

### watch

This tool works best when used with `watch`. This is built in to Linux; it can
be installed on MacOS with:

```shell
brew install watch
```

## Usage

I tend to run the tool with `watch` to pull notifications every minute or so and
give me an (almost) up-to-date view.

### CLI

The default output format is for CLI usage:

```shell
watch -cn60 venv/bin/gh-notifs
```

### Web

I run the tool with `watch` to generate the webpage and then open it in a
browser. The webpage has some JS to make it reload every few seconds to show
updates when they happen.

```shell
watch -n60 venv/bin/gh-notifs -Hf index.html
```

`watch` won't run in the background, so if you don't want to give up a whole
terminal window you can achieve the same thing with

```shell
while true; venv/bin/gh-notifs -Hf index.html; sleep 60; done &
```
