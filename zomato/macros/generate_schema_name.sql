-- Overrides dbt's default schema-naming behaviour, which would otherwise
-- concatenate target.schema + custom_schema (e.g. "staging_marts"). With this
-- override, a model's +schema config (set in dbt_project.yml: staging,
-- marts, snapshot) is used as-is -- so models land in exactly ZOMATO.STAGING /
-- ZOMATO.MARTS / etc. instead of a prefixed hybrid name.
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}
    {%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}