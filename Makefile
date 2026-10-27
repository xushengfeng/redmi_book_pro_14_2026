.PHONY: test install-ready install-agent build-wmi

test:
	python3 -m unittest discover -s tests -v

install-ready:
	sudo ./install-iot-ready.sh

install-agent:
	sudo ./install-iot-agent.sh

build-wmi:
	sudo ./build-redmi-wmi.sh
