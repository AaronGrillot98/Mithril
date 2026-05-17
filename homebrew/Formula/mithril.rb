class Mithril < Formula
  include Language::Python::Virtualenv

  desc "Firewall for LLMs — defends against prompt injection and data exfiltration"
  homepage "https://github.com/AaronGrillot98/mithril"
  url "https://files.pythonhosted.org/packages/source/m/mithril-llm/mithril-llm-0.6.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_AFTER_PYPI_PUBLISH"
  license "Apache-2.0"

  depends_on "python@3.12"

  # Top-level runtime deps only. Sub-deps resolve via pip at install time.
  # When bumping versions, refresh hashes with:
  #   brew update-python-resources mithril
  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/source/f/fastapi/fastapi-0.115.0.tar.gz"
    sha256 "REPLACE_WITH_FASTAPI_SHA256"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/source/u/uvicorn/uvicorn-0.30.0.tar.gz"
    sha256 "REPLACE_WITH_UVICORN_SHA256"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/source/h/httpx/httpx-0.27.2.tar.gz"
    sha256 "REPLACE_WITH_HTTPX_SHA256"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/source/p/pydantic/pydantic-2.9.0.tar.gz"
    sha256 "REPLACE_WITH_PYDANTIC_SHA256"
  end

  resource "pydantic-settings" do
    url "https://files.pythonhosted.org/packages/source/p/pydantic_settings/pydantic_settings-2.5.0.tar.gz"
    sha256 "REPLACE_WITH_PYDANTIC_SETTINGS_SHA256"
  end

  resource "typer" do
    url "https://files.pythonhosted.org/packages/source/t/typer/typer-0.12.5.tar.gz"
    sha256 "REPLACE_WITH_TYPER_SHA256"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.8.0.tar.gz"
    sha256 "REPLACE_WITH_RICH_SHA256"
  end

  resource "jinja2" do
    url "https://files.pythonhosted.org/packages/source/j/jinja2/jinja2-3.1.4.tar.gz"
    sha256 "REPLACE_WITH_JINJA2_SHA256"
  end

  resource "prometheus-fastapi-instrumentator" do
    url "https://files.pythonhosted.org/packages/source/p/prometheus_fastapi_instrumentator/prometheus_fastapi_instrumentator-7.0.0.tar.gz"
    sha256 "REPLACE_WITH_PROM_INSTRUMENTATOR_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "0.6.0", shell_output("#{bin}/mithril version")
  end
end
