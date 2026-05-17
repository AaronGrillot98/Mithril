# conda-forge recipe (staging)

This directory holds the conda-forge recipe for `mithril-llm`. The recipe is
staged in-repo until it's submitted as a PR against
[conda-forge/staged-recipes](https://github.com/conda-forge/staged-recipes).

## Submission checklist

1. After publishing the matching version to PyPI, compute the sdist sha256:

   ```bash
   curl -sL https://pypi.io/packages/source/m/mithril-llm/mithril-llm-0.6.0.tar.gz \
     | shasum -a 256
   ```

2. Replace `REPLACE_WITH_SHA256_AFTER_PYPI_PUBLISH` in `recipe/meta.yaml`.

3. Open a PR to `conda-forge/staged-recipes` adding this recipe under
   `recipes/mithril-llm/meta.yaml`.

4. Wait for the conda-forge bots to approve and the feedstock to be created.
   After that, version bumps land automatically via `regro-cf-autotick-bot`.
