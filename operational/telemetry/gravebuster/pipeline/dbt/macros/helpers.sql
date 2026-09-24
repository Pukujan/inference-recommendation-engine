{% macro clean_path(table) -%}
'{{ var("clean_root") }}/{{ table }}/day=*/*.parquet'
{%- endmacro %}

{% macro skey(cols) -%}
md5(concat_ws('|', {% for c in cols %}coalesce(CAST({{ c }} AS VARCHAR), '~'){% if not loop.last %}, {% endif %}{% endfor %}))
{%- endmacro %}

{% macro snap() -%}
'{{ var("snapshot_id") }}' AS snapshot_id
{%- endmacro %}

{% macro ts_utc(ns) -%}
make_timestamp(({{ ns }}) // 1000)
{%- endmacro %}
