# iCal SEQUENCE regenerated on every download

Some providers (observed on Luma) stamp `SEQUENCE` with a generation
counter that changes on every request, like `DTSTAMP`. Hashing it made every
record look changed on every run, defeating change detection. The second
fetch below differs only in `DTSTAMP` and `SEQUENCE` and must report no
changed records.
