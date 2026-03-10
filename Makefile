CTL := ./lightsctl.sh

.PHONY: help check validate doctor perf benchmark status logs logs-errors tail health diagnose restart update update-qlc backup restore \
        setup harden setup-full add-key disable-password-auth static-ip \
        qlc-headless list-fixtures install-fixture test-dmx deploy-workspace set-default-workspace pull-workspace open ssh wifi wifi-status scan gen-cert ssl-proxy \
        reboot poweroff hdmi-disable os-version landing-setup landing-deploy

help:
	@$(CTL) help

# ── Connectivity ──────────────────────────────────────────────────────────────
check:
	$(CTL) check

validate:
	$(CTL) validate

doctor:
	$(CTL) doctor

# perf usage: make perf [DURATION=30]
perf:
	$(CTL) perf $(DURATION)

benchmark:
	$(CTL) benchmark

# ── Service management ────────────────────────────────────────────────────────
status:
	$(CTL) status

logs:
	$(CTL) logs

logs-errors:
	$(CTL) logs-errors

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

# restore usage: make restore BACKUP=backups/qlcplus-backup-20260305T203838Z.tar.gz
restore:
	$(CTL) restore $(BACKUP)

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

list-fixtures:
	$(CTL) list-fixtures

# install-fixture usage: make install-fixture FIXTURE=path/to/fixture.qxf
install-fixture:
	$(CTL) install-fixture $(FIXTURE)

test-dmx:
	$(CTL) test-dmx

# deploy usage: make deploy WS=workspaces/studio.qxw
deploy:
	$(CTL) deploy-workspace $(WS)

# set-default usage: make set-default WS=workspaces/studio.qxw
set-default:
	$(CTL) set-default-workspace $(WS)

# pull usage: make pull [OUTPUT=custom-name.qxw]
pull:
	$(CTL) pull-workspace $(OUTPUT)

open:
	$(CTL) open-web

# ── Network ───────────────────────────────────────────────────────────────────
ssh:
	$(CTL) ssh

wifi:
	$(CTL) wifi

wifi-status:
	$(CTL) wifi-status

scan:
	$(CTL) scan

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

os-version:
	$(CTL) os-version

# ── Landing page ───────────────────────────────────────────────────────────────
landing-setup:
	$(CTL) landing-setup

landing-deploy:
	$(CTL) landing-deploy
