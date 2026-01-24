# Preset Templates

Pre-configured genloop bundles for common use cases. Each preset includes a `config.yaml` and curated `constraints/` directory.

## Available Presets

| Preset | Description | Constraints |
|--------|-------------|-------------|
| `docs` | Technical documentation, guides, API docs | accuracy, clarity, completeness |
| `code` | Code generation, implementation | correctness, security, testability |
| `stories` | User stories, specifications | acceptance-criteria, scope, testability |

## Usage

Use the `--template` flag with `/arena:genloop-init`:

```bash
# Initialize with docs preset
/arena:genloop-init --template docs

# Initialize with code preset
/arena:genloop-init --template code

# Initialize with stories preset
/arena:genloop-init --template stories
```

This copies the preset's `config.yaml` and `constraints/` to your project's `.arena/` directory.

## Preset Structure

Each preset contains:

```
presets/<name>/
├── config.yaml       # Genloop configuration
└── constraints/      # Curated constraint files
    ├── *.yaml
    └── ...
```

## Customization

After initializing with a preset, you can:

1. Modify `.arena/genloop.yaml` to adjust settings
2. Add/remove constraints from `.arena/constraints/`
3. Edit constraint files to tune severity levels and rules

## Creating Custom Presets

Create a new preset by adding a directory under `presets/`:

```bash
templates/presets/my-preset/
├── config.yaml
└── constraints/
    └── my-constraint.yaml
```

Then use: `/arena:genloop-init --template my-preset`
