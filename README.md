# Espor Satis Rapor

Bu proje, haftalik Excel yuklemelerinden aylik satis/ciro/kar raporu uretir.

## Excel Kolon Eslesmesi

- `R` -> satis adeti
- `S` -> urun adi
- `U` -> urun satis fiyati
- `Y` -> stok kodu

## Kurulum

```bash
python -m pip install -r requirements.txt
```

## Uygulamayi Baslatma

Windows'ta `streamlit` komutu sistemde bulunmayabilir. Bu durumda uygulamayi asagidaki sekilde calistirin:

```bash
python -m streamlit run app.py
```

Alternatif olarak virtual env icinde ise su komut da calisir:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Is Akisi

1. `Excel Yukle` sekmesinden haftalik dosyayi yukle.
2. Ayi belirtmek icin bir tarih sec (dosya o aya yazilir).
3. `Maliyet Girisi` sekmesinde stok kodu bazinda birim maliyet gir.
4. `Aylik Rapor` sekmesinde:
   - Toplam adet
   - Toplam ciro
   - Toplam maliyet
   - Gercek kar
   gor.
