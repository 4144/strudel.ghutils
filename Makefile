
PACKAGE = strudel.ghutils

.PHONY: test
test:
	python -m unittest test

.PHONY: build
build:
	$(MAKE) clean
	$(MAKE) test
	python setup.py sdist bdist_wheel

.PHONY: publish
publish:
	test $$(git config user.name) || git config user.name "semantic-release (via TravisCI)"
	test $$(git config user.email) || git config user.email "semantic-release@travis"
	test $$TRAVIS_TAG && semantic-release publish
	# old way: create ~/.pypirc, then
	# $(MAKE) build
	# twine upload dist/*  # handled by semantic-release in this package

.PHONY: clean
clean:
	rm -rf $(PACKAGE).egg-info dist build docs/build
	find -name "*.pyo" -delete
	find -name "*.pyc" -delete
	find -name __pycache__ -delete

.PHONY: html
html:
	sphinx-build -M html "docs" "docs/build"

.PHONY: install
install:
	pip install -r requirements.txt

.PHONY: install_dev
install_dev:
	$(MAKE) install
	pip install sphinx sphinx-autobuild
	pip install python-semantic-release
