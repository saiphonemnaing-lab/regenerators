from data_processing import download_weather, date_window, BASE
for year in [2022, 2023, 2024]:
    result = download_weather([6,139,226], BASE/'data',
                              date_window(f'{year}-11-01',f'{year+1}-02-28'),
                              date_window('2023-01-01','2025-12-31'))
    print(year, sum(b['records'] for b in result if b['scope']=='hourly'), flush=True)
