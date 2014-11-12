# Author: Eric Kow
# License: CeCILL-B (French BSD3-like)

"""
Experimental sandbox (ignore)
"""

from __future__ import print_function

from educe.util import add_corpus_filters, fields_without
import educe.stac
import educe.stac.annotation

from ..args import\
    add_usual_output_args,\
    get_output_dir, announce_output_dir
from ..output import save_document

NAME = 'tmp'


def config_argparser(parser):
    """
    Subcommand flags.

    You should create and pass in the subparser to which the flags
    are to be added.
    """
    parser.add_argument('corpus', metavar='DIR', help='corpus dir')
    add_corpus_filters(parser, fields=fields_without(["stage"]))
    add_usual_output_args(parser)
    parser.set_defaults(func=main)

# not the same as educe.stac.annotation
RENAMES = {'Strategic_comment': 'Other'}


def main(args):
    """
    Subcommand main.

    You shouldn't need to call this yourself if you're using
    `config_argparser`
    """

    corpus = read_corpus(args,
                         preselected={"stage": ["units"]})
    output_dir = get_output_dir(args)
    for k in corpus:
        doc = corpus[k]
        for edu in filter(educe.stac.is_edu, doc.units):
            etypes = frozenset(educe.stac.split_type(edu))
            etypes2 = frozenset(RENAMES.get(t, t) for t in etypes)
            if etypes != etypes2:
                edu.type = "/".join(sorted(etypes2))
        save_document(output_dir, k, doc)
    announce_output_dir(output_dir)

