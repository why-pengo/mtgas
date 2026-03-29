# Version Management with Bumpver

This project uses [bumpver](https://github.com/mbarkhau/bumpver) to manage version numbers consistently across multiple files.

## Installation

Bumpver is included in the dev dependencies and is installed automatically with:

```bash
make setup
```

Or install it directly:

```bash
pip install bumpver
```

## Configuration

Bumpver is configured in `pyproject.toml` under the `[tool.bumpver]` section:

```toml
[tool.bumpver]
current_version = "0.1.0"
version_pattern = "MAJOR.MINOR.PATCH"
commit_message = "bump version {old_version} -> {new_version}"
commit = true
tag = true
push = false

[tool.bumpver.file_patterns]
"pyproject.toml" = [
    'current_version = "{version}"',
    'version = "{version}"',
]
"src/__init__.py" = [
    '__version__ = "{version}"',
]
```

### Configuration Options

- **current_version**: The current version of the project
- **version_pattern**: The versioning scheme (MAJOR.MINOR.PATCH)
- **commit**: Automatically create a git commit (default: true)
- **tag**: Automatically create a git tag (default: true)
- **push**: Automatically push to remote (default: false for safety)
- **file_patterns**: Files and patterns to update

## Usage

### Show Current Version

```bash
bumpver show --no-fetch
```

### Preview Changes (Dry Run)

Always preview changes before applying them:

```bash
# Patch version (0.1.0 -> 0.1.1)
bumpver update --patch --dry --no-fetch

# Minor version (0.1.0 -> 0.2.0)
bumpver update --minor --dry --no-fetch

# Major version (0.1.0 -> 1.0.0)
bumpver update --major --dry --no-fetch
```

### Update Version

#### Patch Release (Bug fixes)

```bash
bumpver update --patch --no-fetch
```

#### Minor Release (New features)

```bash
bumpver update --minor --no-fetch
```

#### Major Release (Breaking changes)

```bash
bumpver update --major --dry --no-fetch
```

#### Set Specific Version

```bash
bumpver update --set-version "1.2.3" --no-fetch
```

## Semantic Versioning

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** (X.0.0): Incompatible API or configuration changes
- **MINOR** (0.X.0): New features, backward compatible
- **PATCH** (0.0.X): Bug fixes, backward compatible

### When to Bump Each Version

#### Patch (0.1.0 -> 0.1.1)
- Bug fixes
- Documentation updates
- Internal refactoring
- Performance improvements (no breaking changes)

#### Minor (0.1.0 -> 0.2.0)
- New dashboard charts or views
- New log parsing capabilities
- New CLI commands or web UI features
- Deprecations (with backward compatibility)

#### Major (0.1.0 -> 1.0.0)
- Breaking database schema changes
- Incompatible configuration format changes
- Major redesign of the import workflow

## Standard Release Workflow

1. **Make your changes and commit them**
   ```bash
   git add .
   git commit -m "feat: add new feature"
   ```

2. **Ensure all checks pass**
   ```bash
   make ci
   ```

3. **Preview the version bump**
   ```bash
   bumpver update --minor --dry --no-fetch
   ```

4. **Apply the version bump**
   ```bash
   bumpver update --minor --no-fetch
   ```

   This will:
   - Update version in `pyproject.toml` (2 places)
   - Update `__version__` in `src/__init__.py`
   - Create a git commit: `"bump version 0.1.0 -> 0.2.0"`
   - Create a git tag: `v0.2.0`

5. **Push to remote (with tags)**
   ```bash
   git push origin main --tags
   ```

Pushing a `v*.*.*` tag automatically triggers the [Docker publish workflow](.github/workflows/docker-publish.yml), which builds and pushes the container image to the GitHub Container Registry (`ghcr.io`).

## Container Images

Docker images are published to the GitHub Container Registry at:

```
ghcr.io/why-pengo/mtgas
```

Each release tag produces three image tags:

| Image tag | Example | Description |
|-----------|---------|-------------|
| `v{version}` | `v0.2.0` | Exact version |
| `{major}.{minor}` | `0.2` | Latest patch in this minor |
| `{major}` | `0` | Latest minor in this major |
| `latest` | `latest` | Most recent release |

### Pull a specific release

```bash
docker pull ghcr.io/why-pengo/mtgas:v0.2.0
```

### Pull the latest release

```bash
docker pull ghcr.io/why-pengo/mtgas:latest
```

## Files Updated by Bumpver

| File | Pattern |
|------|---------|
| `pyproject.toml` | `version = "X.Y.Z"` and `current_version = "X.Y.Z"` |
| `src/__init__.py` | `__version__ = "X.Y.Z"` |

## Troubleshooting

### Error: "Command 'git fetch' returned non-zero exit status"

Use `--no-fetch` to skip fetching from remote:

```bash
bumpver update --patch --no-fetch
```

### Working Directory Has Uncommitted Changes

```bash
bumpver update --patch --allow-dirty --no-fetch
```

⚠️ Only use `--allow-dirty` if you're confident about your uncommitted changes.

### Verify Configuration

```bash
bumpver show --no-fetch -v
```

## Additional Resources

- [Bumpver Documentation](https://github.com/mbarkhau/bumpver)
- [Semantic Versioning](https://semver.org/)
