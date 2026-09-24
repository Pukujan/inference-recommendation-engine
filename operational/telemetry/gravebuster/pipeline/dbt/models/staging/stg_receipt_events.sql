SELECT * FROM read_parquet('{{ var("clean_root") }}/receipts/receipt_events.parquet')
