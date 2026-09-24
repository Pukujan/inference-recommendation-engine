SELECT * FROM read_parquet({{ clean_path('spans') }}, hive_partitioning = false, union_by_name = true)
