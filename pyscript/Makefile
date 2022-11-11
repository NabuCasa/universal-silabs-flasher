all:
	cd .. && .venv/bin/python setup.py bdist_wheel
	cp -fv ../dist/*.whl .

	cd ../../../bellows && .venv/bin/python setup.py bdist_wheel
	cp -fv ../../../bellows/dist/*.whl .

	python -m http.server