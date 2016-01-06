#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Identify repeat numbers in STR repeats.
"""

import re
import os.path as op
import sys
import logging

from math import log
from collections import defaultdict
from jcvi.graphics.histogram import stem_leaf_plot
from jcvi.utils.cbook import percentage
from jcvi.formats.bed import natsorted
from jcvi.apps.grid import MakeManager
from jcvi.apps.base import OptionParser, ActionDispatcher, mkdir, sh


class STRLine(object):

    def __init__(self, line):
        args = line.split()
        self.seqid = args[0]
        self.start = int(args[1])
        self.end = int(args[2])
        self.period = int(args[3])
        self.copynum = float(args[4])
        self.consensusSize = int(args[5])
        self.pctmatch = int(args[6])
        self.pctindel = int(args[7])
        self.score = int(args[8])
        self.A = int(args[9])
        self.C = int(args[10])
        self.G = int(args[11])
        self.T = int(args[12])
        self.entropy = float(args[13])
        self.motif = args[14]

    def __str__(self):
        return "\t".join(str(x) for x in (self.seqid, self.start, self.end,
                        self.period, self.copynum, self.consensusSize,
                        self.pctmatch, self.pctindel, self.score,
                        self.A, self.C, self.G, self.T,
                        "{0:.2f}".format(self.entropy), self.motif))

    def is_valid(self, maxperiod=6, maxlength=150, minscore=30):
        return 2 <= self.period <= maxperiod and \
               (self.end - self.start + 1) <= maxlength and \
               self.score >= minscore

    def calc_entropy(self):
        total = self.A + self.C + self.G + self.T
        fractions = [x * 1.0 / total for x in [self.A, self.C, self.G, self.T]]
        entropy = sum([-1.0 * x * log(x, 2) for x in fractions if x != 0])
        return entropy

    def iter_exact_str(self, genome):
        pat = re.compile("(({0}){{2,}})".format(self.motif))
        start = self.start
        s = genome[self.seqid][self.start - 1: self.end]
        for m in re.finditer(pat, s):
            self.start = start + m.start()
            length = m.end() - m.start()
            subseq = m.group(0)
            assert length % len(self.motif) == 0
            assert subseq.startswith(self.motif)

            self.end = self.start - 1 + length
            self.copynum = length / len(self.motif)
            self.pctmatch = 100
            self.pctindel = 0
            self.score = 2 * length
            self.A = subseq.count('A')
            self.C = subseq.count('C')
            self.G = subseq.count('G')
            self.T = subseq.count('T')
            self.entropy = self.calc_entropy()
            yield self


def main():

    actions = (
        ('pe', 'infer paired-end reads spanning a certain region'),
        ('htt', 'extract HTT region and run lobSTR'),
        ('lobstr', 'run lobSTR on a big BAM'),
        ('lobstrindex', 'make lobSTR index'),
        ('trf', 'run TRF on FASTA files'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def trf(args):
    """
    %prog trf outdir

    Run TRF on FASTA files.
    """
    from jcvi.apps.base import iglob

    p = OptionParser(trf.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    outdir, = args
    mm = MakeManager()

    params = "2 4090 4090 80 10 30 6".split()
    bedfiles = []
    for fastafile in natsorted(iglob(outdir, "*.fa,*.fasta")):
        pf = op.basename(fastafile).split(".")[0]
        cmd1 = "trf {0} {1} -d -h".format(fastafile, " ".join(params))
        datfile = op.basename(fastafile) + "." + ".".join(params) + ".dat"
        bedfile = "{0}.trf.bed".format(pf)
        cmd2 = "cat {0} | awk '($9 >0)' | sed 's/ /\\t/g'".format(datfile)
        cmd2 += " | awk '{{print \"{0}\\t\" $0}}' > {1}".format(pf, bedfile)
        mm.add(fastafile, datfile, cmd1)
        mm.add(datfile, bedfile, cmd2)
        bedfiles.append(bedfile)

    bedfile = "trf.bed"
    cmd = "cat {0} > {1}".format(" ".join(natsorted(bedfiles)), bedfile)
    mm.add(bedfiles, bedfile, cmd)

    mm.write()


def lobstr(args):
    """
    %prog lobstr bamfile

    Run lobSTR on a big BAM file.
    """
    p = OptionParser(lobstr.__doc__)
    p.set_home("lobstr", default="/mnt/software/lobSTR-4.0.0")
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    bamfile, = args
    lhome = opts.lobstr_home

    mm = MakeManager()
    vcffiles = []
    for chr in range(1, 23) + ["X", "Y"]:
        cmd, vcffile = allelotype_on_chr(bamfile, chr, lhome)
        mm.add(bamfile, vcffile, cmd)
        vcffiles.append(vcffile)

    gzfile = bamfile.split(".")[0] + ".vcf.gz"
    cmd = "vcf-concat {0} | vcf-sort".format(" ".join(vcffiles))
    cmd += " | bgzip -c > {0}".format(gzfile)
    mm.add(vcffiles, gzfile, cmd)

    tbifile = gzfile + ".tbi"
    cmd = "tabix -p vcf {0}".format(gzfile)
    mm.add(gzfile, tbifile, cmd)

    mm.write()


def allelotype_on_chr(bamfile, chr, lhome):
    outfile = "{0}.chr{1}".format(bamfile.split(".")[0], chr)
    cmd = "{0}/bin/allelotype --command classify --bam {1}".format(lhome, bamfile)
    cmd += " --noise_model {0}/models/illumina_v3.pcrfree".format(lhome)
    cmd += " --strinfo {0}/hg38/hg38.tab".format(lhome)
    cmd += " --index-prefix {0}/hg38/lobSTR_".format(lhome)
    cmd += " --chrom chr{0} --out {1}".format(chr, outfile)
    cmd += " --max-diff-ref 150"
    return cmd, outfile + ".vcf"


def htt(args):
    """
    %prog htt bamfile

    Extract HTT region and run lobSTR.
    """
    p = OptionParser(htt.__doc__)
    p.set_home("lobstr")
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    bamfile, = args
    lhome = opts.lobstr_home

    minibamfile = bamfile.split("/")[-1]
    cmd = "samtools view {0} chr4:3070000-3080000 -b".format(bamfile)
    cmd += " -o {0}".format(minibamfile)
    sh(cmd)

    sh("rm {0}.bai".format(minibamfile))
    sh("samtools index {0}".format(minibamfile))

    cmd = allelotype_on_chr(minibamfile, 4, lhome)
    sh(cmd)


def lobstrindex(args):
    """
    %prog lobstrindex hg38.trf.bed hg38.upper.fa

    Make lobSTR index. Make sure the FASTA contain only upper case (so use
    fasta.format --upper to convert from UCSC fasta). The bed file is generated
    by str().
    """
    import pyfasta

    p = OptionParser(lobstrindex.__doc__)
    p.set_home("lobstr")
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    trfbed, fastafile = args
    lhome = opts.lobstr_home
    pf = fastafile.split(".")[0]
    mkdir(pf)

    genome = pyfasta.Fasta(fastafile)
    newbedfile = trfbed + ".new"
    newbed = open(newbedfile, "w")
    fp = open(trfbed)
    retained = total = 0
    for row in fp:
        s = STRLine(row)
        total += 1
        for ns in s.iter_exact_str(genome):
            if not ns.is_valid():
                continue
            print >> newbed, ns
            retained += 1
    newbed.close()
    logging.debug("Retained: {0}".format(percentage(retained, total)))

    mm = MakeManager()
    cmd = "python {0}/scripts/lobstr_index.py".format(lhome)
    cmd += " --str {0} --ref {1} --out {2}".format(newbedfile, fastafile, pf)
    mm.add((newbedfile, fastafile), op.join(pf, "lobSTR_ref.fasta.rsa"), cmd)

    tabfile = "{0}/{0}.tab".format(pf)
    cmd = "python {0}/scripts/GetSTRInfo.py".format(lhome)
    cmd += " {0} {1} > {2}".format(newbedfile, fastafile, tabfile)
    mm.add((newbedfile, fastafile), tabfile, cmd)
    mm.write()


def pe(args):
    """
    %prog pe bam chr start end

    Infer distance paired-end reads spanning a certain region.
    """
    import pysam

    p = OptionParser(pe.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 4:
        sys.exit(not p.print_help())

    bam, chr, start, end = args
    start, end = int(start), int(end)
    target_len = end - start + 1
    pad = 1000  # How far do we look beyond the target
    pstart = start - pad
    pend = end + pad
    logging.debug("Target length={0}".format(target_len))

    samfile = pysam.AlignmentFile(bam, "rb")
    iter = samfile.fetch(chr, pstart, pend)
    cache = defaultdict(list)
    for x in iter:
        if not x.is_paired:
            continue
        cache[x.query_name].append(x)

    tlens = []
    for name, reads in cache.iteritems():
        if len(reads) < 2:
            continue
        a, b = reads[:2]
        # Get all pairs where read1 is on left flank and read2 is on right flank
        if a.reference_start >= start or b.reference_end <= end:
            continue
        for x in (a, b):
            print x.query_name, x.is_reverse, \
                    x.query_alignment_start, x.query_alignment_end, \
                    x.reference_start, x.reference_end, \
                    x.tlen
        print '=' * 60
        tlen = abs(x.tlen)
        tlens.append(tlen)

    stem_leaf_plot(tlens, 300, 600, 20)


if __name__ == '__main__':
    main()