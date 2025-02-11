#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

# Import standard python libraries
import csv
import io
import json
import logging
import os
import rdflib
import rdflib.namespace
import sys

# Import schema.org libraries
if not os.getcwd() in sys.path:
    sys.path.insert(1, os.getcwd())

import software

import software.util.fileutils as fileutils
import software.util.schemaglobals as schemaglobals
import software.util.schemaversion as schemaversion
import software.util.textutils as textutils
import software.util.sdojsonldcontext as sdojsonldcontext
import software.scripts.shex_shacl_shapes_exporter as shex_shacl_shapes_exporter

import software.SchemaTerms.sdotermsource as sdotermsource
import software.SchemaTerms.sdoterm as sdoterm
import software.SchemaExamples.schemaexamples as schemaexamples
import software.SchemaTerms.localmarkdown as localmarkdown

VOCABURI = sdotermsource.SdoTermSource.vocabUri()

log = logging.getLogger(__name__)


def buildTurtleEquivs():
    """Build equivalences to"""
    s_p = "http://schema.org/"
    s_s = "https://schema.org/"
    outGraph = rdflib.Graph()
    outGraph.bind("schema_p", s_p)
    outGraph.bind("schema_s", s_s)
    outGraph.bind("owl", rdflib.namespace.OWL)

    for t in sdotermsource.SdoTermSource.getAllTerms(expanded=True):
        if not t.retired:  # drops non-schema terms and those in attic
            eqiv = rdflib.namespace.OWL.equivalentClass
            if t.termType == sdoterm.SdoTermType.PROPERTY:
                eqiv = rdflib.namespace.OWL.equivalentProperty

            p = rdflib.URIRef(s_p + t.id)
            s = rdflib.URIRef(s_s + t.id)
            outGraph.add((p, eqiv, s))
            outGraph.add((s, eqiv, p))
            log.debug("%s ", t.uri)

    return outGraph.serialize(format="turtle", auto_compact=True, sort_keys=True)


def absoluteFilePath(fn):
    name = os.path.join(schemaglobals.OUTPUTDIR, fn)
    fileutils.checkFilePath(os.path.dirname(name))
    return name


CACHECONTEXT = None


def jsonldcontext(page):
    global CACHECONTEXT
    if not CACHECONTEXT:
        CACHECONTEXT = sdojsonldcontext.createcontext()
    return CACHECONTEXT


def jsonldtree(page):
    global VISITLIST
    VISITLIST = []

    term = {}
    context = {}
    context["rdfs"] = "http://www.w3.org/2000/01/rdf-schema#"
    context["schema"] = "https://schema.org"
    context["rdfs:subClassOf"] = {"@type": "@id"}
    context["description"] = "rdfs:comment"
    context["children"] = {"@reverse": "rdfs:subClassOf"}
    term["@context"] = context
    data = _jsonldtree("Thing", term)
    return json.dumps(data, indent=3)


def _jsonldtree(tid, term=None):
    termdesc = sdotermsource.SdoTermSource.getTerm(tid)
    if not term:
        term = {}
    term["@type"] = "rdfs:Class"
    term["@id"] = "schema:" + termdesc.id
    term["name"] = termdesc.label
    if termdesc.supers:
        sups = []
        for sup in termdesc.supers:
            sups.append("schema:" + sup)
        if len(sups) == 1:
            term["rdfs:subClassOf"] = sups[0]
        else:
            term["rdfs:subClassOf"] = sups
    term["description"] = textutils.ShortenOnSentence(
        textutils.StripHtmlTags(termdesc.comment)
    )
    if termdesc.pending:
        term["pending"] = True
    if termdesc.retired:
        term["attic"] = True
    if tid not in VISITLIST:
        VISITLIST.append(tid)
        if termdesc.subs:
            subs = []
            for sub in termdesc.subs:
                subs.append(_jsonldtree(sub))
            term["children"] = subs
    return term


def httpequivs(page):
    return buildTurtleEquivs()


def owl(page):
    from sdoowl import OwlBuild

    return OwlBuild().getContent()


def sitemap(page):
    node = """ <url>
   <loc>https://schema.org/%s</loc>
   <lastmod>%s</lastmod>
 </url>
"""
    STATICPAGES = [
        "docs/schemas.html",
        "docs/full.html",
        "docs/gs.html",
        "docs/about.html",
        "docs/howwework.html",
        "docs/releases.html",
        "docs/faq.html",
        "docs/datamodel.html",
        "docs/developers.html",
        "docs/extension.html",
        "docs/meddocs.html",
        "docs/hotels.html",
    ]

    output = []
    output.append("""<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
""")
    terms = sdotermsource.SdoTermSource.getAllTerms(suppressSourceLinks=True)
    version_date = schemaversion.getCurrentVersionDate()
    for term in terms:
        if not (term.startswith("http://") or term.startswith("https://")):
            output.append(node % (term, version_date))
    for term in STATICPAGES:
        output.append(node % (term, version_date))
    output.append("</urlset>\n")
    return "".join(output)


def protocolSwap(content, protocol, altprotocol):
    ret = content.replace("%s://schema.org" % protocol, "%s://schema.org" % altprotocol)
    for ext in ["attic", "auto", "bib", "health-lifesci", "meta", "pending"]:
        ret = ret.replace(
            "%s://%s.schema.org" % (protocol, ext),
            "%s://%s.schema.org" % (altprotocol, ext),
        )
    return ret


def protocols():
    """Return the protocols (http, https) in order of priority."""
    vocaburi = sdotermsource.SdoTermSource.vocabUri()
    if vocaburi.startswith("https"):
        return "https", "http"
    return "http", "https"


allGraph = None
currentGraph = None


def exportrdf(exportType):
    global allGraph, currentGraph

    if not allGraph:
        allGraph = rdflib.Graph()
        allGraph.bind("schema", VOCABURI)
        currentGraph = rdflib.Graph()
        currentGraph.bind("schema", VOCABURI)

        allGraph += sdotermsource.SdoTermSource.sourceGraph()

        protocol, altprotocol = protocols()

        deloddtriples = """DELETE {?s ?p ?o}
            WHERE {
                ?s ?p ?o.
                FILTER (! strstarts(str(?s), "%s://schema.org") ).
            }""" % (protocol)
        allGraph.update(deloddtriples)
        currentGraph += allGraph

        desuperseded = """PREFIX schema: <%s://schema.org/>
        DELETE {?s ?p ?o}
        WHERE{
            ?s ?p ?o;
                schema:supersededBy ?sup.
        }""" % (protocol)
        # Currently superseded terms are not suppressed from 'current' file dumps
        # Whereas they are suppressed from the UI
        # currentGraph.update(desuperseded)

        delattic = """PREFIX schema: <%s://schema.org/>
        DELETE {?s ?p ?o}
        WHERE{
            ?s ?p ?o;
                schema:isPartOf <%s://attic.schema.org>.
        }""" % (protocol, protocol)
        currentGraph.update(delattic)

    formats = ["json-ld", "turtle", "nt", "nquads", "rdf"]
    extype = exportType[len("RDFExport.") :]
    if exportType == "RDFExports":
        for format in sorted(formats):
            _exportrdf(format, allGraph, currentGraph)
    elif extype in formats:
        _exportrdf(extype, allGraph, currentGraph)
    else:
        raise Exception("Unknown export format: %s" % exportType)


EXTENSIONS_FOR_FORMAT = {
    "xml": ".xml",
    "rdf": ".rdf",
    "nquads": ".nq",
    "nt": ".nt",
    "json-ld": ".jsonld",
    "turtle": ".ttl",
}

completed = []


def _exportrdf(format, all, current):
    global completed

    protocol, altprotocol = protocols()

    if format in completed:
        return
    else:
        completed.append(format)

    version = schemaversion.getVersion()

    for ver in ["current", "all"]:
        if ver == "all":
            g = all
        else:
            g = current
        if format == "nquads":
            gr = rdflib.Dataset()
            qg = gr.graph(rdflib.URIRef("%s://schema.org/%s" % (protocol, version)))
            qg += g
            g = gr
        fn = absoluteFilePath(
            "releases/%s/schemaorg-%s-%s%s"
            % (version, ver, protocol, EXTENSIONS_FOR_FORMAT[format])
        )
        afn = absoluteFilePath(
            "releases/%s/schemaorg-%s-%s%s"
            % (version, ver, altprotocol, EXTENSIONS_FOR_FORMAT[format])
        )
        fmt = format
        if format == "rdf":
            fmt = "pretty-xml"
        f = open(fn, "w", encoding="utf8")
        af = open(afn, "w", encoding="utf8")
        kwargs = {"sort_keys": True}
        out = g.serialize(format=fmt, auto_compact=True, **kwargs)
        f.write(out)
        af.write(protocolSwap(out, protocol=protocol, altprotocol=altprotocol))
        log.info("Exported %s and %s" % (fn, afn))
        f.close()
        af.close()


def array2str(values):
    if not values:
        return ""
    return ", ".join(values)


def uriwrap(ids):
    if not ids:
        return ""
    if isinstance(ids, list):
        return array2str(map(uriwrap, ids))
    # ids is a single element
    if ids.startswith("http:") or ids.startswith("https:"):
        # external reference
        return ids
    return VOCABURI + ids


def exportcsv(page):
    protocol, altprotocol = protocols()

    typeFields = [
        "id",
        "label",
        "comment",
        "subTypeOf",
        "enumerationtype",
        "equivalentClass",
        "properties",
        "subTypes",
        "supersedes",
        "supersededBy",
        "isPartOf",
    ]
    propFields = [
        "id",
        "label",
        "comment",
        "subPropertyOf",
        "equivalentProperty",
        "subproperties",
        "domainIncludes",
        "rangeIncludes",
        "inverseOf",
        "supersedes",
        "supersededBy",
        "isPartOf",
    ]
    typedata = []
    typedataAll = []
    propdata = []
    propdataAll = []
    terms = sdotermsource.SdoTermSource.getAllTerms(
        expanded=True, suppressSourceLinks=True
    )
    for term in terms:
        if (
            term.termType == sdoterm.SdoTermType.REFERENCE
            or term.id.startswith("http://")
            or term.id.startswith("https://")
        ):
            continue
        row = {}
        row["id"] = term.uri
        row["label"] = term.label
        row["comment"] = term.comment
        row["supersedes"] = uriwrap(term.supersedes)
        row["supersededBy"] = uriwrap(term.supersededBy)
        ext = term.extLayer
        if len(ext):
            ext = "%s://%s.schema.org" % (protocol, ext)
        row["isPartOf"] = ext
        if term.termType == sdoterm.SdoTermType.PROPERTY:
            row["subPropertyOf"] = uriwrap(term.supers)
            row["equivalentProperty"] = array2str(term.equivalents)
            row["subproperties"] = uriwrap(term.subs)
            row["domainIncludes"] = uriwrap(term.domainIncludes)
            row["rangeIncludes"] = uriwrap(term.rangeIncludes)
            row["inverseOf"] = uriwrap(term.inverse)
            propdataAll.append(row)
            if not term.retired:
                propdata.append(row)
        else:
            row["subTypeOf"] = uriwrap(term.supers)
            if term.termType == sdoterm.SdoTermType.ENUMERATIONVALUE:
                row["enumerationtype"] = uriwrap(term.enumerationParent)
            else:
                row["properties"] = uriwrap(term.allproperties)
            row["equivalentClass"] = array2str(term.equivalents)
            row["subTypes"] = uriwrap(term.subs)
            typedataAll.append(row)
            if not term.retired:
                typedata.append(row)

    writecsvout("properties", propdata, propFields, "current", protocol, altprotocol)
    writecsvout("properties", propdataAll, propFields, "all", protocol, altprotocol)
    writecsvout("types", typedata, typeFields, "current", protocol, altprotocol)
    writecsvout("types", typedataAll, typeFields, "all", protocol, altprotocol)


def writecsvout(ftype, data, fields, ver, protocol, altprotocol):
    version = schemaversion.getVersion()
    fn = absoluteFilePath(
        "releases/%s/schemaorg-%s-%s-%s.csv" % (version, ver, protocol, ftype)
    )
    afn = absoluteFilePath(
        "releases/%s/schemaorg-%s-%s-%s.csv" % (version, ver, altprotocol, ftype)
    )
    log.debug("Writing files %s and %s" % (fn, afn))
    csvout = io.StringIO()
    csvfile = open(fn, "w", encoding="utf8")
    acsvfile = open(afn, "w", encoding="utf8")
    writer = csv.DictWriter(
        csvout, fieldnames=fields, quoting=csv.QUOTE_ALL, lineterminator="\n"
    )
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    csvfile.write(csvout.getvalue())
    csvfile.close()
    acsvfile.write(
        protocolSwap(csvout.getvalue(), protocol=protocol, altprotocol=altprotocol)
    )
    acsvfile.close()
    csvout.close()


def jsoncounts(page):
    counts = sdotermsource.SdoTermSource.termCounts()
    counts["schemaorgversion"] = schemaversion.getVersion()
    return json.dumps(counts)


def jsonpcounts(page):
    content = """
    COUNTS = '%s';

    insertschemacounts ( COUNTS );
    """ % jsoncounts(page)
    return content


def exportshex_shacl(page):
    release_dir = os.path.join(
        os.getcwd(), schemaglobals.RELEASE_DIR, schemaversion.getVersion()
    )
    shex_shacl_shapes_exporter.generate_files(
        term_defs_path=os.path.join(release_dir, "schemaorg-all-http.nt"),
        outputdir=release_dir,
        outputfileprefix="schemaorg-",
    )


def examples(page):
    return schemaexamples.SchemaExamples.allExamplesSerialised()


FILELIST = {
    "Context": (
        jsonldcontext,
        [
            "docs/jsonldcontext.jsonld",
            "docs/jsonldcontext.json",
            "docs/jsonldcontext.json.txt",
            "releases/%s/schemaorgcontext.jsonld" % schemaversion.getVersion(),
        ],
    ),
    "Tree": (jsonldtree, ["docs/tree.jsonld"]),
    "jsoncounts": (jsoncounts, ["docs/jsoncounts.json"]),
    "jsonpcounts": (jsonpcounts, ["docs/jsonpcounts.js"]),
    "Owl": (
        owl,
        [
            "docs/schemaorg.owl",
            "releases/%s/schemaorg.owl" % schemaversion.getVersion(),
        ],
    ),
    "Httpequivs": (
        httpequivs,
        ["releases/%s/httpequivs.ttl" % schemaversion.getVersion()],
    ),
    "Sitemap": (sitemap, ["docs/sitemap.xml"]),
    "RDFExports": (exportrdf, [""]),
    "RDFExport.turtle": (exportrdf, [""]),
    "RDFExport.rdf": (exportrdf, [""]),
    "RDFExport.nt": (exportrdf, [""]),
    "RDFExport.nquads": (exportrdf, [""]),
    "RDFExport.json-ld": (exportrdf, [""]),
    "Shex_Shacl": (exportshex_shacl, [""]),
    "CSVExports": (exportcsv, [""]),
    "Examples": (
        examples,
        ["releases/%s/schemaorg-all-examples.txt" % schemaversion.getVersion()],
    ),
}


def buildFiles(files):
    log.debug("Initalizing Mardown")
    software.SchemaTerms.localmarkdown.MarkdownTool.setWikilinkCssClass("localLink")
    software.SchemaTerms.localmarkdown.MarkdownTool.setWikilinkPrePath(
        "https://schema.org/"
    )
    # Production site uses no suffix in link - mapping to file done in server config
    software.SchemaTerms.localmarkdown.MarkdownTool.setWikilinkPostPath("")

    all = ["ALL", "All", "all"]
    for a in all:
        if a in files:
            files = sorted(FILELIST.keys())
            break

    for p in files:
        log.info("Preparing file %s:" % p)
        if p in FILELIST.keys():
            func, filenames = FILELIST.get(p, None)
            if func:
                content = func(p)
                if content:
                    for filename in filenames:
                        fn = absoluteFilePath(filename)
                        with open(fn, "w", encoding="utf8") as handle:
                            handle.write(content)
                        log.info("Created %s" % fn)
        else:
            log.warning("Unknown files name: %s" % p)
