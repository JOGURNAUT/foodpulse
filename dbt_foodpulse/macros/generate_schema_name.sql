{#
  Put each model in the schema its folder declares, instead of dbt's default.

  dbt's built-in behaviour is to PREFIX the target schema onto the custom one:
  a model with schema 'MARTS' running against target schema 'DBT_RAJAT' lands in
  DBT_RAJAT_MARTS. That default exists so two developers building the same
  project do not overwrite each other, and it is the right default for a team.

  Here it is wrong, because the schemas are part of the warehouse's shape --
  RAW, STAGING, MARTS, AI are named in the Snowflake setup SQL, granted to
  DBT_ROLE there, and referenced by the COPY INTO statements. A build that
  invented FOODPULSE_MARTS would leave those grants pointing at nothing.

  So: use the custom schema exactly as written, and fall back to the target's
  own schema when a model declares none.

  The trade is real and worth knowing for when this grows: with this macro, two
  people building at once write to the same tables. The usual answer is to keep
  this behaviour for the production target only and let development targets keep
  dbt's prefixing.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}

    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}

{%- endmacro %}
