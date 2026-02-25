# *Staphylococcus aureus* Typing

This directory contains analyses and results for *Staphylococcus aureus* typing as part of the [AllTheBacteria](https://allthebacteria.readthedocs.io/en/latest/) project.

**Definition:**
> *Staphylococcus aureus* is defined here as all [sylph](https://github.com/bluenote-1577/sylph)-identified *Staphylococcus aureus* genomes from the AllTheBacteria dataset.

**Note:**
- There may be conflicting species designations between sylph and other tools. This means:
  - Some true *S. aureus* genomes may be missing.
  - Some non-*S. aureus* genomes may be included.

**How to download** 
There will be links to OSF that has the results. Open the OSF link from here, and then under the three dots right of the file name, there will be link to download the file under the drop-down. 

## Tools Used

- [mlst](https://github.com/tseemann/mlst) v2.23.0
  - [MLST scheme](https://pubmlst.org/organisms/staphylococcus-aureus) (schema downloaded from pubmlst on 2025-08-22)
  - Note: Anonymous access to pubmlst likely includes data up to 2024-12-31; this is the database version used here.

The full list of software and dependencies is available in [pixi_env.txt](pixi_env.txt).

## MLST Typing

Genomes were typed using [multi-locus sequence typing (MLST)](https://github.com/tseemann/mlst) with the Achtman scheme.

MLST was installed using `pixi`. I used the instructions on the `mlst` github to [update the database](https://github.com/tseemann/mlst?tab=readme-ov-file#updating-the-database). 

MLST was run on our HPC cluster, and jobs and results were handled by my dodgy python script. For completness, this is included here under [dodgy_scripts/mlst.py](dodgy_scripts/mlst.py). This script is not intended for public consumption. 

**Results:**

| File/Resource                                                                | Description                                                                  |
|------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [Manifest (tab-delimited)](https://osf.io/zgr6q)                             | Status (PASS, FAIL, NOT DONE) for each genome; manifest of included genomes. |
| [Raw MLST results table](https://osf.io/ypzbq)                               | Unmodified results table output from MLST software.                          |
| [MLST results for *S. aureus* (tab-delimited)](https://osf.io/8zyrd)         | Final processed MLST results for *S. aureus*.                                |

I have opted to alter the raw results from the MLST software. The specific loci and number of loci for different schemes varies, and to keep the results table consisent, I have opted to keep the allele numbers as comma-seperated values in one column called "Alleles". 

Hence, the results file will look like this: 

```
Sample	Scheme	ST	Alleles
SAMN18191XXXX   saureus	5	arcC(1),aroE(4),glpF(1),gmk(4),pta(12),tpi(1),yqiL(10)
....
```



## Contact

For questions regarding *E. coli* typing, please contact [Nabil-Fareed Alikhan](mailto:nabil@happykhan.com).
