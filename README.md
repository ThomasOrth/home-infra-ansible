# home-infra

Run locally example: `ansible-playbook init.yml --tag=zsh --connection=local -i localhost,  --extra-vars "os_user=prof" --ask-become-pass`

Run init with askpass: `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook init.yml -i inventories/home-infra/hosts.yml --ask-pass --ask-become-pass --limit minecraft.local`