CTL := ./lightsctl.sh

.PHONY: help check status logs tail health diagnose restart update update-qlc backup \
        setup harden setup-full add-key disable-password-auth static-ip \
        qlc-headless deploy-workspace open ssh wifi wifi-status gen-cert ssl-proxy \
        reboot poweroff hdmi-disable landing-setup landing-deploy

help:
	@$(CTL) help

# ── Connectivity ──────────────────────────────────────────────────────────────
check:
	$(CTL) check

# ── Service management ────────────────────────────────────────────────────────
status:
	$(CTL) status

logs:
	$(CTL) logs

tail:
	$(CTL) tail

health:
	$(CTL) health

diagnose:
	$(CTL) diagnose

restart:
	$(CTL) restart

# ── Updates ───────────────────────────────────────────────────────────────────
update:
	$(CTL) update

update-qlc:
	$(CTL) update-qlc

backup:
	$(CTL) backup

# ── Provisioning ──────────────────────────────────────────────────────────────
setup:
	$(CTL) setup

harden:
	$(CTL) harden

setup-full:
	$(CTL) setup-full

add-key:
	$(CTL) add-key

disable-password-auth:
	$(CTL) disable-password-auth

# static-ip usage: make static-ip IP=192.168.1.50/24 GW=192.168.1.1
static-ip:
	$(CTL) static-ip $(IP) $(GW) $(DNS)

# ── QLC+ ──────────────────────────────────────────────────────────────────────
qlc-headless:
	$(CTL) qlc-headless

# deploy usage: make deploy WS=workspaces/studio.qxw
deploy:
	$(CTL) deploy-workspace $(WS)

open:
	$(CTL) open-web

# ── Network ───────────────────────────────────────────────────────────────────
ssh:
	$(CTL) ssh

wifi:
	$(CTL) wifi

wifi-status:
	$(CTL) wifi-status

# ── TLS ───────────────────────────────────────────────────────────────────────
gen-cert:
	$(CTL) gen-cert

ssl-proxy:
	$(CTL) ssl-proxy

# ── System ────────────────────────────────────────────────────────────────────
reboot:
	$(CTL) reboot

poweroff:
	$(CTL) poweroff

hdmi-disable:
	$(CTL) hdmi-disable

# ── Landing page ───────────────────────────────────────────────────────────────
landing-setup:
	$(CTL) landing-setup

landing-deploy:
	$(CTL) landing-deploy
