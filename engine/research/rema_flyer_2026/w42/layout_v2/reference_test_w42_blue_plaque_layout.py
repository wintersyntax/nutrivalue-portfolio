"""Offline synthetic checks for FØR→blue-plaque→printed-title proposals.

Only source OCR text/boxes can supply amounts and product evidence. No writes
outside temporary images; no network, runtime OCR, cloud or database access.
"""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from ad_layout_regions import (_blue_price_badge_boxes,
                               link_prior_prices_by_regions)


def ln(text,x,y,w,h):
    return {'text':text,'box':{'x':x,'y':y,'width':w,'height':h}}


def offer(number,title,price,page):
    return {'offer_number':number,'title':title,'catalog_price_nok':price,'page':page}


def flyer(directory,page,plaque_rects, *, plain=True):
    # Normalized top-origin coordinates, square vibrant blue plaques.
    w,h=900,1300
    img=np.full((h,w,3),(195,225,245),dtype=np.uint8)
    for x0,y0,x1,y1 in plaque_rects:
        cv2.rectangle(img,(round(x0*w),round(y0*h)),(round(x1*w),round(y1*h)),
                      (164,61,4),-1)
        if not plain:
            cv2.putText(img,'39', (round(x0*w)+45,round(y0*h)+85),
                        cv2.FONT_HERSHEY_SIMPLEX,2,(255,255,255),7)
    path=Path(directory)/f'page-{page:02}.jpg'
    assert cv2.imwrite(str(path),img)
    return path


def run(page,lines,offers,directory,*,prior_lines=(0,),amount='54.90'):
    priors=[{'kind':'prior_price','page':page,'signal_id':f'prior-{i}',
             'line_index':i,'context':lines[i]['text'],'box':lines[i]['box']}
            for i in prior_lines]
    with patch('ad_layout_regions._prior_money',return_value=Decimal(amount)):
        return link_prior_prices_by_regions(
            {'pages':[{'page':page,'lines':lines}]},priors,offers,image_dir=directory)


class BluePlaqueLayoutTests(unittest.TestCase):
    def test_large_photograph_between_price_and_caption(self):
        with TemporaryDirectory() as d:
            flyer(d,7,[(.53,.16,.93,.43)])
            lines=[ln('FØR 54,90',.49,.827,.22,.025),
                   ln('34.90',.61,.65,.20,.07),
                   ln('UTVALGT KJELDSBERG KAFFE Kjeldsberg',.33,.090,.43,.021)]
            found=run(7,lines,[offer(11,'UTVALGT KJELDSBERG KAFFE','34.90',7)],d)[0]
            self.assertEqual(found['status'],'region_candidate_review_only')
            self.assertEqual(found['candidate_products'][0]['offer_number'],11)
            self.assertEqual(found['candidate_products'][0]['match_method'],
                             'blue_plaque_vertical_layout_review_only')
            self.assertFalse(found['old_price_verified'])
            self.assertFalse(found['budget_price_modified'])

    def test_caption_below_pictured_package_without_expanding_original_cell(self):
        with TemporaryDirectory() as d:
            flyer(d,15,[(.21,.16,.50,.354),(.82,.69,.965,.79)])
            lines=[ln('FØR 39,90',.18,.830,.15,.02),
                   ln('34.90',.24,.700,.20,.08),
                   ln('SKINKE XXL',.18,.351,.19,.021),
                   ln('UTVALGT PÅLEGG',.32,.10,.27,.025)]
            found=run(15,lines,[offer(28,'SKINKE XXL','34.90',15),
                                 offer(43,'UTVALGT PÅLEGG','29.90',15)],d,
                      amount='39.90')[0]
            self.assertEqual(found['candidate_products'][0]['offer_number'],28)
            self.assertEqual(found['region']['status'],
                             'blue_plaque_vertical_ad_hypothesis_review_only')
            self.assertIn('first_pass_region_for_diagnostics',found['region'])

    def test_embedded_compact_amount_only_when_pixel_plaque_supports_it(self):
        with TemporaryDirectory() as d:
            flyer(d,16,[(.266,.604,.467,.740),(.732,.604,.933,.740)])
            lines=[ln('FØR 50,90',.245,.391,.111,.014),
                   ln('HATT 3990',.12,.287,.31,.08),
                   ln('HANDVA Veggen spelt',.12,.20,.31,.030),
                   ln('FØR 70,90',.71,.391,.11,.014),
                   ln('5990',.75,.29,.12,.08),
                   ln('PROCIUTTO CRUDO',.65,.10,.24,.024)]
            offers=[offer(40,'VEGGEN SPELT','39.90',16),
                    offer(41,'PROCIUTTO CRUDO','59.90',11)]
            found=run(16,lines,offers,d,prior_lines=(0,),amount='50.90')[0]
            self.assertEqual(found['status'],'region_candidate_review_only')
            self.assertEqual(found['candidate_products'][0]['offer_number'],40)
            self.assertEqual(found['candidate_products'][0]['current_ocr_parse'],
                             'blue_plaque_embedded_compact_ocr_review_only')

    def test_without_image_no_mixed_embedded_compact_price(self):
        with TemporaryDirectory() as d:
            lines=[ln('FØR 50,90',.245,.391,.111,.014),
                   ln('HATT 3990',.12,.287,.31,.08),
                   ln('HANDVA Veggen spelt',.12,.20,.31,.03)]
            found=run(16,lines,[offer(40,'VEGGEN SPELT','39.90',16)],d,
                      amount='50.90')[0]
            self.assertEqual(found['candidate_products'],[])

    def test_four_digits_on_package_without_blue_price_plaque_not_used(self):
        with TemporaryDirectory() as d:
            flyer(d,16,[(.77,.18,.95,.32)]) # unrelated badge
            lines=[ln('FØR 50,90',.245,.391,.111,.014),
                   ln('HATT 3990',.12,.287,.31,.08),
                   ln('HANDVA Veggen spelt',.12,.20,.31,.03)]
            found=run(16,lines,[offer(40,'VEGGEN SPELT','39.90',16)],d,
                      amount='50.90')[0]
            self.assertEqual(found['candidate_products'],[])

    def test_neighbor_badge_cannot_lend_printed_price_to_wrong_prior(self):
        with TemporaryDirectory() as d:
            flyer(d,16,[(.266,.604,.467,.740),(.732,.604,.933,.740)])
            lines=[ln('FØR 50,90',.245,.391,.111,.014),
                   ln('5990',.75,.29,.12,.08),
                   ln('HANDVA Veggen spelt',.12,.20,.31,.03)]
            found=run(16,lines,[offer(40,'VEGGEN SPELT','59.90',16)],d,
                      amount='70.90')[0]
            self.assertEqual(found['candidate_products'],[])

    def test_two_possible_products_same_price_abstains(self):
        with TemporaryDirectory() as d:
            flyer(d,15,[(.21,.16,.50,.354)])
            lines=[ln('FØR 39,90',.18,.830,.15,.02),
                   ln('34.90',.24,.700,.20,.08),
                   ln('SKINKE XXL',.18,.351,.19,.021)]
            offers=[offer(28,'SKINKE XXL','34.90',15),
                    offer(128,'SKINKE XXL','34.90',4)]
            found=run(15,lines,offers,d,amount='39.90')[0]
            self.assertEqual(found['candidate_products'],[])

    def test_neighbor_same_row_caption_not_claimed(self):
        with TemporaryDirectory() as d:
            flyer(d,16,[(.266,.604,.467,.740),(.732,.604,.933,.740)])
            lines=[ln('FØR 50,90',.245,.391,.111,.014),
                   ln('3990',.31,.29,.12,.08),
                   ln('VEGGEN SPELT',.67,.18,.18,.03)] # caption under RIGHT badge
            found=run(16,lines,[offer(40,'VEGGEN SPELT','39.90',11)],d,
                      amount='50.90')[0]
            self.assertEqual(found['candidate_products'],[])

    def test_price_range_unchanged_even_with_pixels(self):
        with TemporaryDirectory() as d:
            flyer(d,7,[(.53,.16,.93,.43)])
            lines=[ln('FØR 54,90-60,90',.49,.827,.22,.025),
                   ln('34.90',.61,.65,.20,.07),
                   ln('UTVALGT KJELDSBERG KAFFE',.33,.090,.43,.021)]
            found=run(7,lines,[offer(11,'UTVALGT KJELDSBERG KAFFE','34.90',7)],d)[0]
            self.assertEqual(found['status'],'unresolved_prior_price_range')
            self.assertEqual(found['candidate_products'],[])

    def test_badge_only_cannot_fabricate_ocr_current_amount(self):
        with TemporaryDirectory() as d:
            flyer(d,7,[(.53,.16,.93,.43)])
            lines=[ln('FØR 54,90',.49,.827,.22,.025),
                   ln('BLURRED',.61,.65,.20,.07),
                   ln('UTVALGT KJELDSBERG KAFFE',.33,.090,.43,.021)]
            found=run(7,lines,[offer(11,'UTVALGT KJELDSBERG KAFFE','34.90',7)],d)[0]
            self.assertEqual(found['candidate_products'],[])

    def test_reused_plaque_for_two_old_prices_cannot_be_shared(self):
        with TemporaryDirectory() as d:
            flyer(d,7,[(.53,.16,.93,.43)])
            lines=[ln('FØR 54,90',.49,.827,.22,.025),
                   ln('FØR 50,90',.49,.837,.22,.025),
                   ln('34.90',.61,.65,.20,.07),
                   ln('UTVALGT KJELDSBERG KAFFE',.33,.090,.43,.021)]
            found=run(7,lines,[offer(11,'UTVALGT KJELDSBERG KAFFE','34.90',7)],d,
                      prior_lines=(0,1))
            self.assertTrue(all(not r['candidate_products'] for r in found))


if __name__=='__main__':unittest.main()
