# Homebrew tap (staging)

This directory holds a draft Homebrew formula for `mithril`. It is staged
in-repo until it's pushed to a dedicated tap repo
(`AaronGrillot98/homebrew-mithril`).

## Publishing checklist

1. Create the tap repo:

   ```bash
   gh repo create AaronGrillot98/homebrew-mithril --public \
     --description "Homebrew tap for Mithril (LLM firewall)"
   ```

2. Copy `Formula/mithril.rb` into the new repo's root `Formula/` directory.

3. Resolve the placeholder SHA-256 hashes:

   ```bash
   curl -sL https://files.pythonhosted.org/packages/source/m/mithril-llm/mithril-llm-0.6.0.tar.gz \
     | shasum -a 256
   ```

   For each `resource "<name>"` block, fetch the matching sdist tarball and
   replace the placeholder hash. `brew update-python-resources mithril`
   automates this end-to-end once the formula is in a tap repo.

4. Push the formula and verify:

   ```bash
   brew tap AaronGrillot98/mithril
   brew install mithril
   mithril version
   ```
