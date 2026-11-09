"""Convert text in the legacy TCVN3 (ABC) Vietnamese encoding to Unicode, printing the result.

    python scripts/sheet-varieties/mock/tcvn3_to_unicode.py extracted.txt > unicode.txt

For text extracted from older PDFs whose fonts use TCVN3 (e.g. `.VnTime`), such as the
geographical-indication records of the Cục Sở hữu trí tuệ: pdftotext gives Latin-1 look-alike
characters, which this maps back to Vietnamese letters, one character at a time. Lower case and the
TCVN3 capitals ĂÂĐÊÔƠƯ are mapped; other capitals in TCVN3 fonts use a separate font and are left as
they are. Documents that mix TCVN3 and Unicode come out with a few wrong letters, so check names and
figures against another source.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
if len(sys.argv) != 2:
    sys.exit(__doc__)
M = {'¸':'á','µ':'à','μ':'à','¶':'ả','·':'ã','¹':'ạ','¨':'ă','¾':'ắ','»':'ằ','¼':'ẳ','½':'ẵ','Æ':'ặ','©':'â','Ê':'ấ','Ç':'ầ','È':'ẩ','É':'ẫ','Ë':'ậ',
     '®':'đ','Ð':'é','Ì':'è','Î':'ẻ','Ï':'ẽ','Ñ':'ẹ','ª':'ê','Õ':'ế','Ò':'ề','Ó':'ể','Ô':'ễ','Ö':'ệ','Ý':'í','×':'ì','Ø':'ỉ','Ü':'ĩ','Þ':'ị',
     'ã':'ó','ß':'ò','á':'ỏ','â':'õ','ä':'ọ','«':'ô','è':'ố','å':'ồ','æ':'ổ','ç':'ỗ','é':'ộ','¬':'ơ','í':'ớ','ê':'ờ','ë':'ở','ì':'ỡ','î':'ợ',
     'ó':'ú','ï':'ù','ñ':'ủ','ò':'ũ','ô':'ụ','\xad':'ư','−':'ư','ø':'ứ','õ':'ừ','ö':'ử','÷':'ữ','ù':'ự','ý':'ý','ú':'ỳ','û':'ỷ','ü':'ỹ','þ':'ỵ',
     '¡':'Ă','¢':'Â','§':'Đ','£':'Ê','¤':'Ô','¥':'Ơ','¦':'Ư'}
t = open(sys.argv[1], encoding='utf-8').read()
# Characters with no TCVN3 mapping (including Unicode Vietnamese already in the text) pass through unchanged.
print(''.join(M.get(ch, ch) for ch in t))
