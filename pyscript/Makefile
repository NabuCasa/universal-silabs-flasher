all:
	cd .. && .venv/bin/python setup.py bdist_wheel
	cp -fv ../dist/*.whl .

	python -m http.server