# plugin_template — Plugin SDK Examples

Index of all template types with when to use each one.

| Template | When to use |
|----------|-------------|
| [Source Plugin](source_plugin.md) | For new data sources (API, scraping, etc.) |
| [Sink Plugin](sink_plugin.md) | For custom outputs (CMS, database, messaging) |
| [Scorer/Normalizer Plugin](scorer_plugin.md) | For custom processing nodes in the pipeline |

## General guidelines

- All plugins should include a `PluginMetadata` instance.
- Plugins should be registered in `pyproject.toml` under the appropriate `job_ftch.*` entry-point group.
- Keep dependencies minimal. Use optional dependency groups in your `pyproject.toml` if the plugin requires heavy libraries.
