"""This module provides a growing library of functions to quantitatively
examine the situated version of the STAC corpus, in itself and with
respect to the purely linguistic (spect) version.
"""

from __future__ import absolute_import, print_function

import copy
from glob import glob
from itertools import chain
import os
import warnings

import pandas as pd

from educe.stac.annotation import (
    is_dialogue, is_edu, is_paragraph, is_preference, is_relation_instance,
    is_resource, is_turn, SUBORDINATING_RELATIONS, COORDINATING_RELATIONS
)
from educe.stac.corpus import Reader as StacReader
from educe.stac.graph import Graph


# naming schemes for games in each season
GAME_GLOBS = {
    'pilot': ['pilot*'],
    'socl-season1': ['s1-league*-game*'],
    'socl-season2': ['s2-league*-game*', 's2-practice*'],
}

# basic layout of the corpus: a few games from all seasons are explicitly set
# out for TEST, the rest are implicitly in TRAIN
SPLIT_GLOBS = {
    'TRAIN': [os.path.join(folder, file_glob)
              for folder, file_glob in chain(
                      (k, x) for k, v in GAME_GLOBS.items()
                      for x in v)],
    'TEST': [os.path.join('TEST', file_glob)
             for file_glob in set(chain.from_iterable(
                     GAME_GLOBS.values()))]
}

# base folder for each version
BASE_SPECT = ''
BASE_SITU = 'situated-annotation'

# TMP derogatory layout for _spect, should be fixed eventually
SPLIT_GLOBS_SPECT = {
    'TRAIN': [os.path.join(folder, file_glob)
              for folder, file_glob in chain(
                      (k + '_spect', x) for k, v in GAME_GLOBS.items()
                      for x in v)],
    'TEST': [os.path.join('TEST_spect', file_glob)
             for file_glob in set(chain.from_iterable(
                     GAME_GLOBS.values()))]
}

# list of DataFrames (names)
DF_NAMES = ['turns', 'dlgs', 'segs', 'acts', 'schms', 'schm_mbrs',
            'disc_rels', 'res', 'pref', 'unit_rels']

# column names for the DataFrames
UNIT_COLS = [
    # identification
    'global_id',
    'doc',
    'subdoc',
    'stage',
    'annotator',
    # type, span, text
    'type',
    'span_beg',
    'span_end',
    'text',
    # metadata
    'creation_date',
    'author',
    'last_modif_date',  # optional?
    'last_modifier',  # optional?
]

TURN_COLS = UNIT_COLS + [
    'timestamp',
    'turn_id',
    'emitter',
    'developments',
    'resources',
    'comments',
]

SEG_COLS = UNIT_COLS + [
    'seg_idx',
    'eeu_idx',
    'edu_idx',
]

ACT_COLS = [
    'global_id',  # foreign key to SEG
    'surface_act',
    'addressee',
]

DLG_COLS = UNIT_COLS + [
    'gets',
    'trades',
    'dice_rolls',
]

RES_COLS = UNIT_COLS + [
    'status',
    'kind',
    'correctness',
    'quantity',
]

PREF_COLS = UNIT_COLS

SCHM_COLS = [
    # identification
    'global_id',
    'doc',
    'subdoc',
    'stage',
    'annotator',
    # type
    'type',
    # metadata
    'creation_date',
    'author',
    'last_modif_date',  # optional?
    'last_modifier',  # optional?
    # features?
    'operator',
    'default',
]

SCHM_MBRS_COLS = [
    'member_id',  # foreign key: global_id of schema or seg
    'schema_id',  # foreign key: global_id of schema
]

REL_COLS = [
    # identification
    'global_id',
    'doc',
    'subdoc',
    'stage',
    'annotator',
    # type
    'type',
    # metadata
    'creation_date',
    'author',
    'last_modif_date',  # optional?
    'last_modifier',  # optional?
    # features
    'arg_scope',  # req?
    'comments',  # opt?
    # endpoints
    'source',
    'target',
]


def compute_rel_attributes(seg_df, rel_df):
    """Compute additional attributes on relations.

    As of 2017-06-29, this only works for relations from the "discourse"
    stage, not "units".

    Parameters
    ----------
    seg_df : DataFrame
        Segments from a game.
    rel_df : DataFrame
        Relations from a game.

    Returns
    -------
    rel_df : DataFrame
        Modified relations.
    """
    discrel_types = frozenset(SUBORDINATING_RELATIONS +
                              COORDINATING_RELATIONS)
    len_segs = []
    len_edus = []
    len_eeus = []
    for _, row in rel_df[['source', 'target', 'type']].iterrows():
        if row['type'] not in discrel_types:
            # non-discourse relations, eg. anaphoric :
            # don't compute length for the moment
            # len_segs.append(None)
            # len_edus.append(None)
            # len_eeus.append(None)
            raise ValueError("Unable to compute the length of a "
                             "non-discourse relation: {}".format(row['type']))
        # discourse relations
        seg_src = seg_df[
            (seg_df['global_id'] == row['source'])
        ]
        seg_tgt = seg_df[
            (seg_df['global_id'] == row['target'])
        ]
        # compute length of attachment
        try:
            len_seg = (seg_tgt['seg_idx'].values[0] -
                       seg_src['seg_idx'].values[0])
        except IndexError:
            print(row)
            print('tgt', seg_tgt)
            print('src', seg_src)
            raise
        len_segs.append(len_seg)
        len_edu = (seg_tgt['edu_idx'].values[0] -
                   seg_src['edu_idx'].values[0])
        len_edus.append(len_edu)
        len_eeu = (seg_tgt['eeu_idx'].values[0] -
                   seg_src['eeu_idx'].values[0])
        len_eeus.append(len_eeu)
    rel_df['len_seg'] = pd.Series(len_segs)
    rel_df['len_edu'] = pd.Series(len_edus)
    rel_df['len_eeu'] = pd.Series(len_eeus)
    return rel_df


def read_game_as_dataframes(game_folder, sel_annotator=None, thorough=True,
                            strip_cdus=False, attach_len=False):
    """Read an annotated game as dataframes.

    Parameters
    ----------
    game_folder : path
        Path to the game folder.
    sel_annotator : str, optional
        Identifier of the annotator whose version we want. If `None`,
        the existing metal annotator will be used (BRONZE|SILVER|GOLD).
    thorough : boolean, defaults to True
        If True, check that annotations in 'units' and 'unannotated'
        that are expected to have a strict equivalent in 'dialogue'
        actually do.
    strip_cdus : boolean, defaults to False
        If True, strip CDUs with the "head" strategy and sloppy=True.
    attach_len : boolean, defaults to False
        If True, compute attachment length. This requires
        strip_cdus=True.

    Returns
    -------
    dfs : tuple of DataFrame
        DataFrames for the annotated game.
    """
    if sel_annotator is None:
        sel_annotator = 'metal'

    df_turns = []  # turns
    df_segs = []  # segments: EDUs, EEUs
    df_dlgs = []  # dialogues
    df_schms = []  # schemas: CDUs
    df_schm_mbrs = []  # schema members
    df_disc_rels = []  # discourse relations
    df_acts = []  # dialogue acts
    df_res = []  # resources
    df_pref = []  # preferences
    df_unit_rels = []  # relations from the "units" stage (anaphora)

    print(game_folder)  # DEBUG
    game_upfolder, game_name = os.path.split(game_folder)
    game_corpus = StacReader(game_upfolder).slurp(doc_glob=game_name)
    # give integer indices to segments, and EDUs in particular
    seg_idx = 0
    eeu_idx = 0
    edu_idx = 0
    for doc_key, doc_val in sorted(game_corpus.items()):
        doc = doc_key.doc
        subdoc = doc_key.subdoc
        stage = doc_key.stage
        annotator = doc_key.annotator
        # skip docs not from a selected annotator
        if ((sel_annotator == 'metal' and
             annotator not in ('BRONZE', 'SILVER', 'GOLD')) or
            (sel_annotator != 'metal' and
             annotator != sel_annotator)):
            continue
        # process annotations in doc
        # print(doc, subdoc, stage, annotator)  # verbose
        doc_text = doc_val.text()
        # print(doc_text)
        for anno in sorted(doc_val.units, key=lambda x: x.span):
            # attributes common to all units
            unit_dict = {
                # identification
                'global_id': anno.identifier(),
                'doc': doc,
                'subdoc': subdoc,
                'stage': stage,
                'annotator': annotator,
                # type, span, text
                'type': anno.type,
                'span_beg': anno.span.char_start,
                'span_end': anno.span.char_end,
                'text': doc_val.text(span=anno.span),
                # metadata
                'creation_date': anno.metadata['creation-date'],
                'author': anno.metadata['author'],
                # optional?
                'last_modifier': anno.metadata.get('lastModifier', None),
                'last_modif_date': anno.metadata.get('lastModificationDate', None),
            }

            # fields specific to each type of unit
            if is_paragraph(anno):
                # paragraph: ignore? one per turn
                pass
            elif is_turn(anno):
                # turn
                # comments = anno.features['Comments']
                # if comments == 'Please write in remarks...':
                unit_dict.update({
                    # features
                    'timestamp': anno.features['Timestamp'],
                    'comments': anno.features['Comments'],
                    'developments': anno.features['Developments'],
                    'turn_id': anno.features['Identifier'],
                    'emitter': anno.features['Emitter'],
                    'resources': anno.features['Resources'],
                })
                if stage == 'discourse':
                    df_turns.append(unit_dict)
                elif thorough:
                    pass  # FIXME check existence (exact duplicate)
            elif is_edu(anno):
                # segment: EDU or EEU
                if stage == 'discourse':
                    if anno.features:
                        raise ValueError('Wow, a discourse segment has *features*')
                    # assign index among segments, across the whole doc
                    unit_dict['seg_idx'] = seg_idx
                    seg_idx += 1
                    if anno.type == 'NonplayerSegment':  # EEU
                        unit_dict['eeu_idx'] = eeu_idx
                        eeu_idx += 1
                    else:  # EDU
                        unit_dict['edu_idx'] = edu_idx
                        edu_idx += 1
                    #
                    df_segs.append(unit_dict)
                elif stage == 'units':
                    # each entry (should) correspond to an entry in df_segs
                    act_dict = {
                        'global_id': anno.identifier(),  # foreign key
                        'surface_act': anno.features['Surface_act'],
                        'addressee': anno.features['Addressee'],
                    }
                    assert (sorted(anno.features.keys()) ==
                            ['Addressee', 'Surface_act'])
                    df_acts.append(act_dict)
                if thorough and stage in ('units', 'unannotated'):
                    # maybe metadata in 'units' has changed? eg. last
                    # modification date, last modifier
                    pass  # FIXME check existence (exact duplicate)
            elif is_dialogue(anno):
                expected_dlg_features = set(
                    ['Dice_rolling', 'Gets', 'Trades'])
                if set(anno.features.keys()).issubset(expected_dlg_features):
                    unit_dict.update({
                        # features
                        'gets': anno.features.get('Gets', None),
                        'trades': anno.features.get('Trades', None),
                        'dice_rolls': anno.features.get('Dice_rolling', None),
                    })
                else:
                    warn_msg = 'Dialogue {}: unexpected features {}'.format(
                        anno.identifier(),
                        ', '.join(x for x in sorted(anno.features.keys())
                                  if x not in set(expected_dlg_features)))
                    warnings.warn(warn_msg)

                if stage == 'discourse':
                    df_dlgs.append(unit_dict)
                elif thorough:
                    pass  # FIXME check existence (exact duplicate)
            elif is_resource(anno):
                unit_dict.update({
                    # features
                    'status': anno.features['Status'],
                    'kind': anno.features['Kind'],
                    'correctness': anno.features['Correctness'],
                    'quantity': anno.features['Quantity'],
                })
                assert (sorted(anno.features.keys()) ==
                        ['Correctness', 'Kind', 'Quantity', 'Status'])
                df_res.append(unit_dict)
            elif is_preference(anno):
                if anno.features:
                    print(anno.__dict__)
                    raise ValueError('Preference with features {}'.format(
                        anno.features))
                df_pref.append(unit_dict)
            else:
                print(anno.__dict__)
                raise ValueError('what unit is this?')
            # print('Unit', anno)

        for anno in doc_val.schemas:
            # in 'discourse': CDUs ;
            # in 'units': combinations of resources (OR, AND)
            schm_dict = {
                # identification
                'global_id': anno.identifier(),
                'doc': doc,
                'subdoc': subdoc,
                'stage': stage,
                'annotator': annotator,
                # type
                'type': anno.type,
                # metadata
                'creation_date': anno.metadata['creation-date'],
                'author': anno.metadata['author'],
                # optional? metadata
                'last_modifier': anno.metadata.get('lastModifier', None),
                'last_modif_date': anno.metadata.get('lastModificationDate', None),
            }
            # assumption: no feature
            if anno.features:
                if stage == 'units':
                    if anno.features.keys() == ['Operator']:
                        schm_dict.update({
                            'operator': anno.features['Operator'],
                        })
                    else:
                        print(anno.origin)
                        print(anno.__dict__)
                        print(anno.features)
                        raise ValueError('{}: schema with *features*'.format(
                            stage))
                elif stage == 'discourse':
                    # tolerate 'default': 'default' for the moment, but
                    # should probably cleaned out
                    if anno.features.keys() == ['default']:
                        schm_dict.update({
                            'default': anno.features['default'],
                        })
                    else:
                        print(anno.origin)
                        print(anno.__dict__)
                        print(anno.features)
                        raise ValueError('{}: schema with *features*'.format(
                            stage))
            df_schms.append(schm_dict)
            # associate to this schema each of its members ; assumptions:
            # - members should be units or schemas (no relation)
            if anno.relations:
                raise ValueError('Wow, a schema with *relation members*')
            for member in anno.members:
                member_dict = {
                    'member_id': member.identifier(),
                    'schema_id': anno.identifier(),
                }
                df_schm_mbrs.append(member_dict)
            # TODO post-verification: check that all members do exist
            # (should be useless as stac-check should catch it)

        # RELATIONS
        # * rewrite endpoints of relations if strip_cdus
        if strip_cdus:
            endpts = dict()  # map relation ids to (src_id, tgt_id)
            dgr = Graph.from_doc(game_corpus, doc_key)
            dgraph = copy.deepcopy(dgr)
            dgraph.strip_cdus(sloppy=True, mode='head')
            for edge in dgraph.relations():
                if "asoubeille_1414085458642" in edge:
                    print('Wop', edge)
                    raise ValueError('gni')
                links = dgraph.links(edge)
                # get the identifiers of the relation and its endpoints
                # to replace CDU ids with segment indices
                anno_rel = dgraph.annotation(edge)
                # as of 2017-06-24, anno_rel has no origin (why?) at
                # this point
                anno_rel.origin = doc_key  # temporary(?) fix
                #
                anno_src = dgraph.annotation(links[0])
                anno_tgt = dgraph.annotation(links[1])
                gid_rel = anno_rel.identifier()
                if gid_rel.endswith('_0'):
                    # strip_cdus appends an integer to each copy of
                    # the relation ; with mode="head", we only expect
                    # one such copy per relation so "_0" should be a
                    # sufficient match, which we can cut off for the
                    # mapping
                    gid_rel = gid_rel[:-2]
                gid_src = anno_src.identifier()
                gid_tgt = anno_tgt.identifier()
                endpts[gid_rel] = (gid_src, gid_tgt)
        # * process relations
        for anno in doc_val.relations:
            # attributes common to all(?) types of annotations
            # * global ids of the relation and its endpoints
            gid_rel = anno.identifier()
            gid_src = anno.source.identifier()
            gid_tgt = anno.target.identifier()
            # * build dict
            rel_dict = {
                # identification
                'global_id': gid_rel,
                'doc': doc,
                'subdoc': subdoc,
                'stage': stage,
                'annotator': annotator,
                # type
                'type': anno.type,
                # metadata
                'last_modifier': anno.metadata['lastModifier'],
                'last_modif_date': anno.metadata['lastModificationDate'],
                'creation_date': anno.metadata['creation-date'],
                'author': anno.metadata['author'],
            }
            # attributes specific to relations
            if 'Argument_scope' not in anno.features:
                # required feature
                w_msg = '{}: relation {} has no Argument_scope'.format(
                    str(doc_key), anno.identifier()
                )
                warnings.warn(w_msg)
            # if strip_cdus, replace endpoints of *discourse* relations
            # with segment ids
            if strip_cdus and is_relation_instance(anno):
                gid_src, gid_tgt = endpts[gid_rel]

            rel_dict.update({
                # features
                'arg_scope': anno.features.get('Argument_scope', None), # req
                'comments': anno.features.get('Comments', None),  # opt
                # endpoints
                'source': gid_src,
                'target': gid_tgt,
            })
            if stage == 'discourse':
                df_disc_rels.append(rel_dict)
            elif stage == 'units':
                df_unit_rels.append(rel_dict)
            else:
                raise ValueError(
                    "relation from stage not in {'units', 'discourse'}")
            

    # create dataframes
    df_turns = pd.DataFrame(df_turns, columns=TURN_COLS)
    df_dlgs = pd.DataFrame(df_dlgs, columns=DLG_COLS)
    df_segs = pd.DataFrame(df_segs, columns=SEG_COLS)
    df_acts = pd.DataFrame(df_acts, columns=ACT_COLS)
    df_schms = pd.DataFrame(df_schms, columns=SCHM_COLS)
    df_schm_mbrs = pd.DataFrame(df_schm_mbrs, columns=SCHM_MBRS_COLS)
    df_disc_rels = pd.DataFrame(df_disc_rels, columns=REL_COLS)
    df_unit_rels = pd.DataFrame(df_unit_rels, columns=REL_COLS)
    df_res = pd.DataFrame(df_res, columns=RES_COLS)
    df_pref = pd.DataFrame(df_pref, columns=PREF_COLS)

    # add columns computed from other dataframes
    # * for segments: retrieve the turn_id and the char positions of the
    # beg and end of the segment in the turn text
    def get_seg_turn_cols(seg):
        """Helper to retrieve turn info for a segment (EDU, EEU)."""
        doc = seg['doc']
        subdoc = seg['subdoc']
        seg_beg = seg['span_beg']
        seg_end = seg['span_end']
        cand_turns = df_turns[(df_turns['span_beg'] <= seg_beg) &
                              (seg_end <= df_turns['span_end']) &
                              (doc == df_turns['doc']) &
                              (subdoc == df_turns['subdoc'])]
        # NB: cand_turns should contain a unique turn
        # compute the beg and end (char) positions of the segment in the turn
        # so we can match between the situated and linguistic versions when
        # the segmentation has changed
        turn_text = cand_turns['text'].item()
        seg_text = seg['text']
        turn_span_beg = turn_text.find(seg_text)
        turn_span_end = turn_span_beg + len(seg_text)
        turn_dict = {
            'turn_id': cand_turns['turn_id'].item(),
            'turn_span_beg': turn_span_beg,
            'turn_span_end': turn_span_end,
        }
        return pd.Series(turn_dict)

    seg_turn_cols = df_segs.apply(get_seg_turn_cols, axis=1)
    df_segs = pd.concat([df_segs, seg_turn_cols], axis=1)
    # * length of attachments
    # 2017-06-29 restricted to *discourse* relations, for the time being
    if strip_cdus and attach_len:
        df_disc_rels = compute_rel_attributes(df_segs, df_disc_rels)

    return (df_turns, df_dlgs, df_segs, df_acts, df_schms, df_schm_mbrs,
            df_disc_rels, df_res, df_pref, df_unit_rels)


def read_corpus_as_dataframes(stac_data_dir, version='situated', split='all',
                              strip_cdus=False, attach_len=False,
                              sel_games=None, exc_games=None):
    """Read the entire corpus as dataframes.

    Parameters
    ----------
    stac_data_dir : str
        Path to the STAC data folder, eg. `/path/to/stac/svn/data`.
    version : one of {'ling', 'situated'}, defaults to 'situated'
        Version of the corpus we want to examine.
    split : one of {'all', 'train', 'test'}, defaults to 'all'
        Split of the corpus.
    strip_cdus : boolean, defaults to False
        If True, strip CDUs with sloppy=True, mode='head'.
    attach_len : boolean, defaults to False
        If True, compute attachment length for relations. Currently
        requires strip_cdus=True.
    sel_games : list of str, optional
        List of selected games. If `None`, all games for the selected
        version and split.
    exc_games : list of str, optional
        List of excluded games. If `None`, all games for the selected
        version and split. Applies after, hence overrides, `sel_games`.

    Returns
    -------
    dfs : tuple of DataFrame
        Dataframes for turns, segments, acts...
    """
    if version not in ('ling', 'situated'):
        raise ValueError("Version must be one of {'ling', 'situated'}")
    if version == 'situated':
        base_dir = BASE_SITU
        all_globs = SPLIT_GLOBS
    else:
        base_dir = BASE_SPECT
        all_globs = SPLIT_GLOBS_SPECT

    if split not in ('all', 'train', 'test'):
        raise ValueError("Split must be one of {'all', 'train', 'test'}")
    if split == 'all':
        sel_globs = list(chain.from_iterable(all_globs.values()))
    elif split == 'train':
        sel_globs = all_globs['TRAIN']
    else:
        sel_globs = all_globs['TEST']

    sel_globs = [os.path.join(stac_data_dir, base_dir, x) for x in sel_globs]
    game_folders = list(chain.from_iterable(glob(x) for x in sel_globs))
    # map games to their folders
    game_dict = {os.path.basename(x): x for x in game_folders}
    if sel_games is not None:
        game_dict = {k: v for k, v in game_dict.items()
                     if k in sel_games}
    if exc_games is not None:
        game_dict = {k: v for k, v in game_dict.items()
                     if k not in exc_games}
    # lists of dataframes
    # TODO dataframe of docs? or glozz documents = subdocs?
    # what fields should be included?
    turn_dfs = []
    dlg_dfs = []
    seg_dfs = []
    act_dfs = []
    schm_dfs = []
    schm_mbr_dfs = []
    disc_rel_dfs = []
    unit_rel_dfs = []
    res_dfs = []
    pref_dfs = []
    for game_name, game_folder in game_dict.items():
        # TMP 2017-06-23 skip unfinished games
        if (version == 'situated' and
            (game_folder.endswith('s2-league5-game2') or
             game_folder.endswith('s2-league8-game2') or
             game_folder.endswith('s2-practice3') or
             game_folder.endswith('s2-practice4'))):
            # skip for now
            continue
        # end TMP

        game_dfs = read_game_as_dataframes(
            game_folder, strip_cdus=strip_cdus, attach_len=attach_len)
        turn_dfs.append(game_dfs[0])
        dlg_dfs.append(game_dfs[1])
        seg_dfs.append(game_dfs[2])
        act_dfs.append(game_dfs[3])
        schm_dfs.append(game_dfs[4])
        schm_mbr_dfs.append(game_dfs[5])
        disc_rel_dfs.append(game_dfs[6])
        res_dfs.append(game_dfs[7])
        pref_dfs.append(game_dfs[8])
        unit_rel_dfs.append(game_dfs[9])
    # concatenate each list into a single dataframe
    turns = pd.concat(turn_dfs, ignore_index=True)
    dlgs = pd.concat(dlg_dfs, ignore_index=True)
    segs = pd.concat(seg_dfs, ignore_index=True)
    acts = pd.concat(act_dfs, ignore_index=True)
    schms = pd.concat(schm_dfs, ignore_index=True)
    schm_mbrs = pd.concat(schm_mbr_dfs, ignore_index=True)
    disc_rels = pd.concat(disc_rel_dfs, ignore_index=True)
    unit_rels = pd.concat(unit_rel_dfs, ignore_index=True)
    res = pd.concat(res_dfs, ignore_index=True)
    pref = pd.concat(pref_dfs, ignore_index=True)
    return (turns, dlgs, segs, acts, schms, schm_mbrs, disc_rels, res, pref,
            unit_rels)


def load_corpus_dataframes(base_dir, dump_fmt='csv'):
    """Load the dataframes from a corpus dump.

    Parameters
    ----------
    base_dir : str
        Path to the base folder for the dump.
    dump_fmt : str, one of {'csv', 'pickle'}
        Format of the dump ; determines the exact path as base_dir/subdir.

    Returns
    -------
    corpus_dfs : tuple of DataFrame
        Corpus DataFrames.
    """
    if dump_fmt not in ('csv', 'pickle'):
        raise ValueError("dump_fmt must be one of {'csv', 'pickle'}")
    base_dir = os.path.abspath(base_dir)
    dump_dir = os.path.join(base_dir, dump_fmt)
    if not os.path.exists(dump_dir) or not os.path.isdir(dump_dir):
        raise ValueError("Unable to find data at {}".format(dump_dir))
    # loading function
    load_fns = {
        'csv': lambda p: pd.DataFrame.from_csv(p, sep='\t', encoding='utf-8'),
        'pickle': lambda p: pd.read_pickle(p),
    }
    load_fn = load_fns[dump_fmt]
    #
    dfs = []
    for df_name in DF_NAMES:
        fname = os.path.join(dump_dir, df_name)
        df = load_fn(fname)
        dfs.append(df)
    return tuple(dfs)


def dump_corpus_dataframes(corpus_dfs, out_dir, out_fmt='csv'):
    """Dump the dataframes for a corpus to a folder.

    Parameters
    ----------
    corpus_dfs : tuple of DataFrame
        Corpus DataFrames, assumed to be in order: turns, dlgs, segs,
        acts, schms, schm_mbrs, rels, res, pref.
    out_dir : str
        Output folder.
    out_fmt : one of {'csv', 'pickle', 'feather'}
        Output format.
    """
    # dump function
    dump_fns = {
        'csv': lambda x, p: x.to_csv(path_or_buf=p, sep='\t',
                                     encoding='utf-8'),
        'pickle': lambda x, p: x.to_pickle(p),
        'feather': lambda x, p: x.to_feather(p),
    }
    if out_fmt not in dump_fns.keys():
        raise ValueError('out_fmt needs to be one of {}'.format(
            out_fmts.keys()))
    dump_fn = dump_fns[out_fmt]  # dump function
    # output dir
    out_dir = os.path.abspath(os.path.join(out_dir, out_fmt))
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    # do dump
    for df_name, df in zip(DF_NAMES, corpus_dfs):
        out_path = os.path.join(out_dir, df_name)
        dump_fn(df, out_path)


if __name__ == '__main__':
    # situated games that are still incomplete, so should be excluded
    not_ready = []  # ['s2-league3-game5', 's2-league4-game2']
    sel_games = None  # ['pilot14']
    # read the situated version
    turns_situ, dlgs_situ, segs_situ, acts_situ, schms_situ, schm_mbrs_situ, rels_situ, res_situ, pref_situ = read_corpus_as_dataframes(version='situated', split='all', sel_games=sel_games, exc_games=not_ready)
    # print(segs_situ[:5])
    if False:
        print(dlgs_situ[:5])
        print(segs_situ[:5])
        print(acts_situ[:5])
        print(schms_situ[:5])
        print(schm_mbrs_situ[:5])
        print(rels_situ[:5])
        print(res_situ[:5])
        print(pref_situ[:5])

    # get the list of documents in the situated version, filter _spect to keep
    # them (only)
    games_situ = list(turns_situ['doc'].unique())

    # read the spect version
    turns_spect, dlgs_spect, segs_spect, acts_spect, schms_spect, schm_mbrs_spect, rels_spect, res_spect, pred_spect = read_corpus_as_dataframes(version='ling', split='all', sel_games=games_situ)
    # print(segs_spect[:5])
    if False:
        print(dlgs_spect[:5])
        print(acts_spect[:5])
        print(schms_spect[:5])
        print(schm_mbrs_spect[:5])
        print(rels_spect[:5])
        print(res_spect[:5])
        print(pref_spect[:5])

    # compare Dialog Act annotations between the two versions ; on common
    # turns, they should be (almost) 100% identical
    seg_acts_spect = pd.merge(segs_spect, acts_spect, on=['global_id'],
                              how='inner')
    seg_acts_situ = pd.merge(segs_situ, acts_situ, on=['global_id'],
                             how='inner')
    seg_acts_union = pd.merge(
        seg_acts_spect, seg_acts_situ,
        on=['doc', 'turn_id', 'turn_span_beg', 'turn_span_end'],
        how='outer',
        indicator=True
    )
    # find EDUs that exist in both, only in _spect, only in _situ
    seg_acts_both = seg_acts_union[
        seg_acts_union['_merge'] == 'both']
    seg_acts_spect_only = seg_acts_union[
        seg_acts_union['_merge'] == 'left_only']
    seg_acts_situ_only = seg_acts_union[
        seg_acts_union['_merge'] == 'right_only']
    print('#EDUs: common / _spect only / _situ only:',
          seg_acts_both.shape[0],
          seg_acts_spect_only.shape[0],
          seg_acts_situ_only.shape[0])
    # focus on different EDUs in common turns
    print('EDUs in _spect only')
    print(seg_acts_spect_only)
    print('vs their counterparts in _situ')
    # set of common turns, as tuples (doc, turn_id), where segments differ
    diff_turn_segs = set(
        tuple(x) for x in seg_acts_spect_only[['doc', 'turn_id']].values)
    # 
    diff_situ_mask = seg_acts_situ_only.apply(
        lambda x: tuple(x[['doc', 'turn_id']].values) in diff_turn_segs,
        axis=1
    )
    print(seg_acts_situ_only[diff_situ_mask])
    print('Differing turns', diff_turn_segs)
    # same EDUs but different dialogue acts
    print('-------------------------')
    diff_acts_mask = (
        (seg_acts_both['surface_act_x'] != seg_acts_both['surface_act_y']) &
        (seg_acts_both['addressee_x'] != seg_acts_both['addressee_y'])
    )
    diff_acts = seg_acts_both[diff_acts_mask]
    sel_cols = [
        'doc', 'turn_id', 'turn_span_beg', 'turn_span_end',
        'subdoc_x', 'global_id_x', 'text_x',
        'surface_act_x', 'addressee_x',
        'subdoc_y', 'global_id_y', 'text_y',
        'surface_act_y', 'addressee_y'
    ]
    if diff_acts.shape[0] > 0:
        print('Changed EDU acts: {} / {}'.format(
            diff_acts.shape[0], seg_acts_both.shape[0]))
        print(diff_acts[sel_cols][:15])
    else:
        print('No changed EDU acts')

    raise ValueError("hop")
